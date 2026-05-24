# print_bridge.py
import os
from tag_engine import TagFactory  # Assuming your previous code is in tag_engine.py


class PrintBridge:
    def __init__(self):
        self.factory = TagFactory()

    def handle_print_request(self, item_data):
        """
        This is the function you will expose to your JavaScript.
        item_data: A dictionary containing wt, size, touch, and tag_id.
        """
        try:
            # 1. Generate the Bitmap
            tag_image = self.factory.generate_tag_image(item_data)

            # 2. (Optional) Save a preview for the UI to show if needed
            # preview_path = os.path.join("ui", "temp", "last_tag_preview.png")
            # tag_image.save(preview_path)

            # 3. Send to Printer
            self.factory.print_to_thermal_printer(tag_image)

            return {"status": "success", "message": "Tag sent to printer"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


# Create a global instance to be used by the main application
printer_bridge = PrintBridge()