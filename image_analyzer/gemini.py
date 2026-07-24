import os
import sys

# Import official Google Gen AI SDK modules
from google import genai
from google.genai import types


def get_gemini_description(config, img_path, location_context, first_match_clean):
    """Invokes Gemini 1.5 Flash using the official GenAI client library."""
    if not config.gemini_api_key:
        return ""

    system_instruction = None
    if config.gemini_system_prompt_file and os.path.exists(config.gemini_system_prompt_file):
        try:
            with open(config.gemini_system_prompt_file, "r", encoding="utf-8") as f:
                raw_prompt = f.read().strip()

                # Dynamic template replacements
                raw_prompt = raw_prompt.replace("{{ camera_name }}", location_context)
                system_instruction = raw_prompt.replace("{{ trigger_name }}", first_match_clean)

        except Exception as e:
            print(f"Error reading Gemini system prompt file: {e}", file=sys.stderr)

    try:
        # Initialize the official SDK client
        client = genai.Client(
            api_key=config.gemini_api_key,
            http_options=types.HttpOptions(timeout=int(config.gemini_timeout * 1000))
        )

        with open(img_path, "rb") as image_file:
            image_bytes = image_file.read()

        # Build inline image part using the library type helper
        image_part = types.Part.from_bytes(
            data=image_bytes,
            mime_type="image/jpeg"
        )

        # Prepare configuration block (handling optional system instruction)
        gen_config = None
        if system_instruction:
            gen_config = types.GenerateContentConfig(system_instruction=system_instruction)
        gemini_model = config.gemini_model

        # Execute content generation via SDK
        response = client.models.generate_content(
            model=gemini_model,
            contents=[
                system_instruction,
                image_part
            ],
            config=gen_config
        )

        if response.text:
            return response.text.strip()

    except Exception as e:
        print(f"Failed to fetch description from Gemini: {e}", file=sys.stderr)
    return ""
