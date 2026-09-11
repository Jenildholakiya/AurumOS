/* AurumOS — GST registration helpers for the setup and settings screens.
 *
 * The same rules as core/gst.py, so the field can go red as it is typed
 * instead of only when the form is submitted. Python remains the authority:
 * every save is re-checked there, and this file can never be the only thing
 * standing between a typo and a tax invoice.
 *
 * Lives in ui/ deliberately. That is the half of the app an update actually
 * reaches, so a correction here lands on a shop counter with a push, while
 * the Python side waits for a rebuild.
 *
 * Exposes window.AurumGST.
 */
(function () {
  'use strict';
  if (window.AurumGST) return;

  var ALPHANUM = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ';

  // Position 1-2 of a GSTIN, including the union territories added in 2020.
  var STATE_CODES = {
    '01': 'Jammu and Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab',
    '04': 'Chandigarh', '05': 'Uttarakhand', '06': 'Haryana', '07': 'Delhi',
    '08': 'Rajasthan', '09': 'Uttar Pradesh', '10': 'Bihar', '11': 'Sikkim',
    '12': 'Arunachal Pradesh', '13': 'Nagaland', '14': 'Manipur',
    '15': 'Mizoram', '16': 'Tripura', '17': 'Meghalaya', '18': 'Assam',
    '19': 'West Bengal', '20': 'Jharkhand', '21': 'Odisha',
    '22': 'Chhattisgarh', '23': 'Madhya Pradesh', '24': 'Gujarat',
    '25': 'Daman and Diu',
    '26': 'Dadra and Nagar Haveli and Daman and Diu',
    '27': 'Maharashtra', '28': 'Andhra Pradesh (old)', '29': 'Karnataka',
    '30': 'Goa', '31': 'Lakshadweep', '32': 'Kerala', '33': 'Tamil Nadu',
    '34': 'Puducherry', '35': 'Andaman and Nicobar Islands',
    '36': 'Telangana', '37': 'Andhra Pradesh', '38': 'Ladakh',
    '97': 'Other Territory', '99': 'Centre Jurisdiction'
  };

  // Articles of jewellery of precious metal: 3%, split half and half within a
  // state. Offered as a starting point, not as advice.
  var DEFAULT_HSN = '7113';
  var DEFAULT_CGST = 1.5;
  var DEFAULT_SGST = 1.5;

  var GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$/;
  var PAN_RE = /^[A-Z]{5}[0-9]{4}[A-Z]$/;

  function normalise(v) {
    return String(v == null ? '' : v).replace(/[\s\-]/g, '').toUpperCase();
  }

  function checksumChar(first14) {
    var total = 0;
    for (var i = 0; i < first14.length; i++) {
      var v = ALPHANUM.indexOf(first14.charAt(i)) * (i % 2 ? 2 : 1);
      total += Math.floor(v / 36) + (v % 36);
    }
    return ALPHANUM.charAt((36 - (total % 36)) % 36);
  }

  function validate(input) {
    var g = normalise(input);
    var out = { ok: false, gstin: g, error: '', state_code: '', state: '',
                pan: '', entity_code: '' };
    if (!g) { out.error = 'Enter a GSTIN.'; return out; }
    if (g.length !== 15) {
      out.error = 'A GSTIN is 15 characters. This one is ' + g.length + '.';
      return out;
    }
    if (!GSTIN_RE.test(g)) {
      out.error = 'That is not the shape of a GSTIN (15 characters: 22AAAAA0000A1Z5).';
      return out;
    }
    var code = g.slice(0, 2);
    if (!STATE_CODES[code]) {
      out.error = code + ' is not a GST state code.';
      return out;
    }
    if (g.charAt(14) !== checksumChar(g.slice(0, 14))) {
      out.error = 'The check character is wrong, so there is a typo somewhere in the number.';
      return out;
    }
    out.ok = true;
    out.state_code = code;
    out.state = STATE_CODES[code];
    out.pan = g.slice(2, 12);
    out.entity_code = g.charAt(12);
    return out;
  }

  function validatePan(p) { return PAN_RE.test(normalise(p)); }

  function cleanRate(v, fallback) {
    if (fallback === undefined) fallback = 0;
    var r = parseFloat(String(v == null ? '' : v).replace('%', '').trim());
    if (!isFinite(r) || r < 0 || r > 100) return fallback;
    return Math.round(r * 1000) / 1000;
  }

  /* Wire a GSTIN box to live feedback.
   *
   * opts: { input, note, state, pan, onChange }  — ids or elements; all but
   * input are optional. The note goes green with the state name when the
   * number checks out, red with the reason when it does not, and silent while
   * it is still too short to judge. Nagging someone mid-typing trains them to
   * ignore the message by the time it is right.
   */
  function attach(opts) {
    opts = opts || {};
    function el(x) { return typeof x === 'string' ? document.getElementById(x) : x; }
    var input = el(opts.input);
    if (!input) return null;
    var note = el(opts.note), stateEl = el(opts.state), panEl = el(opts.pan);

    function paint() {
      var raw = normalise(input.value);
      if (input.value !== raw) {
        var at = input.selectionStart;
        input.value = raw;
        try { input.setSelectionRange(at, at); } catch (e) {}
      }
      var r = validate(raw);
      var judged = raw.length >= 15;
      if (note) {
        if (!raw) { note.textContent = ''; note.className = 'gst-note'; }
        else if (r.ok) {
          note.textContent = r.state + ' · PAN ' + r.pan;
          note.className = 'gst-note ok';
        } else if (judged) {
          note.textContent = r.error;
          note.className = 'gst-note bad';
        } else {
          note.textContent = raw.length + ' of 15';
          note.className = 'gst-note';
        }
      }
      if (stateEl) stateEl.value = r.ok ? r.state : '';
      if (panEl && r.ok && !panEl.value) panEl.value = r.pan;
      input.classList.toggle('gst-bad', judged && !r.ok);
      input.classList.toggle('gst-ok', r.ok);
      if (typeof opts.onChange === 'function') opts.onChange(r);
      return r;
    }

    input.addEventListener('input', paint);
    input.addEventListener('blur', paint);
    input.setAttribute('maxlength', '15');
    input.setAttribute('autocapitalize', 'characters');
    input.setAttribute('spellcheck', 'false');
    if (input.value) paint();
    return { check: paint, validate: function () { return validate(input.value); } };
  }

  /* Read the GST block off a form. Returns {fields, error}.
   * Mirrors core/gst.build: an unregistered shop is never asked to produce a
   * valid number, and what it typed is kept so the toggle is reversible.
   */
  function collect(ids) {
    ids = ids || {};
    function val(id, dflt) {
      var e = document.getElementById(id);
      if (!e) return dflt === undefined ? '' : dflt;
      return e.type === 'checkbox' ? (e.checked ? '1' : '0') : e.value;
    }
    var registered = val(ids.registered || 'gst-registered', '0') === '1';
    var fields = {
      gst_registered: registered ? '1' : '0',
      gstin: normalise(val(ids.gstin || 'gst-gstin')),
      gst_legal_name: String(val(ids.legalName || 'gst-legal-name')).trim(),
      gst_cgst_rate: String(cleanRate(val(ids.cgst || 'gst-cgst'), DEFAULT_CGST)),
      gst_sgst_rate: String(cleanRate(val(ids.sgst || 'gst-sgst'), DEFAULT_SGST)),
      gst_hsn: String(val(ids.hsn || 'gst-hsn', DEFAULT_HSN)).replace(/[^0-9]/g, '').slice(0, 8),
      gst_price_inclusive: val(ids.inclusive || 'gst-inclusive', '0') === '1' ? '1' : '0',
      pan: normalise(val(ids.pan || 'gst-pan'))
    };
    if (!registered) return { fields: fields, error: '' };
    var r = validate(fields.gstin);
    if (!r.ok) return { fields: fields, error: r.error };
    fields.gst_state = r.state;
    fields.gst_state_code = r.state_code;
    if (!validatePan(fields.pan)) fields.pan = r.pan;
    return { fields: fields, error: '' };
  }

  /* Put stored values back on a form. */
  function fill(data, ids) {
    data = data || {}; ids = ids || {};
    function set(id, v, isCheck) {
      var e = document.getElementById(id);
      if (!e) return;
      if (isCheck) e.checked = String(v) === '1';
      else if (v !== undefined && v !== null) e.value = v;
    }
    set(ids.registered || 'gst-registered', data.gst_registered, true);
    set(ids.gstin || 'gst-gstin', data.gstin);
    set(ids.legalName || 'gst-legal-name', data.gst_legal_name);
    set(ids.cgst || 'gst-cgst', data.gst_cgst_rate || DEFAULT_CGST);
    set(ids.sgst || 'gst-sgst', data.gst_sgst_rate || DEFAULT_SGST);
    set(ids.hsn || 'gst-hsn', data.gst_hsn || DEFAULT_HSN);
    set(ids.inclusive || 'gst-inclusive', data.gst_price_inclusive, true);
    set(ids.pan || 'gst-pan', data.pan);
    set(ids.state || 'gst-state', data.gst_state);
  }

  window.AurumGST = {
    STATE_CODES: STATE_CODES,
    DEFAULT_HSN: DEFAULT_HSN,
    DEFAULT_CGST: DEFAULT_CGST,
    DEFAULT_SGST: DEFAULT_SGST,
    normalise: normalise,
    checksumChar: checksumChar,
    validate: validate,
    validatePan: validatePan,
    cleanRate: cleanRate,
    attach: attach,
    collect: collect,
    fill: fill
  };
})();
