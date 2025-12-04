import os
import textwrap
import traceback
import numpy as np
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import moviepy.editor as mp
import google.generativeai as genai
from PIL import Image, ImageDraw, ImageFont

# -------- PIL Resize Compatibility for Pillow >= 10 ----------
try:
    PIL_RESAMPLE = Image.Resampling.LANCZOS
except:
    PIL_RESAMPLE = Image.LANCZOS

# ---------- FLASK APP ----------
app = Flask(__name__)
CORS(app)  # allow calls from your HTML frontend

# ---------- CONFIG ----------
OUTPUT_DIR = "static/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

SECRET_API_KEY = os.getenv("GEMINI_API_KEY")

# ---------- HELPERS: SERVE FRONTEND & VIDEO ----------
@app.route("/")
def index():
    # Serve your HTML if it's in the same folder (index.html)
    return send_from_directory(".", "index.html")

@app.route("/static/output/<path:filename>")
def serve_output(filename):
    return send_from_directory(OUTPUT_DIR, filename)

# ---------- FROM YOUR CODE: REMOVE BLACK BG ----------
def remove_black_background(image_path):
    image = Image.open(image_path).convert("RGBA")
    datas = image.getdata()
    
    new_data = []
    for item in datas:
        # If the pixel is black or very dark (sum of RGB < 50), make it transparent
        if sum(item[:3]) < 50: 
            new_data.append((item[0], item[1], item[2], 0))  # fully transparent
        else:
            new_data.append(item)
    
    image.putdata(new_data)
    return image

# ---------- FROM YOUR CODE: TEXT CLIP (PIL) ----------
def create_text_clip_pil(text, fontsize=40, color='black', width=1000,
                         font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):

    try:
        font = ImageFont.truetype(font_path, fontsize)
    except IOError:
        font = ImageFont.load_default()
        print("Warning: Custom font not found, using default.")

    dummy_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    
    lines = textwrap.wrap(text, width=32)
    final_text = "\n".join(lines)
    
    bbox = dummy_draw.multiline_textbbox((0, 0), final_text, font=font, align="center")
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    img_w = int(text_width + 100)
    img_h = int(text_height + 50)
    
    img = Image.new('RGBA', (img_w, img_h), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    
    draw.multiline_text(
        (img_w/2, img_h/2), 
        final_text, 
        font=font, 
        fill=color, 
        anchor="mm", 
        align="center"
    )
    
    numpy_img = np.array(img)
    return mp.ImageClip(numpy_img)

# ---------- FROM YOUR CODE: GEMINI LOGIC ----------
def generate_viral_content(user_api_key, video_description, manual_text):

    api_key = SECRET_API_KEY if SECRET_API_KEY else user_api_key
    
    if not api_key:
        return "Error: No API Key.", "Error: No API Key."

    try:
        genai.configure(api_key=api_key)
        
        try:
            model = genai.GenerativeModel('gemini-2.5-flash')
        except:
            model = genai.GenerativeModel('gemini-1.5-flash')

        if manual_text:
            overlay_text = manual_text
        else:
            prompt_overlay = f"""
            Role: You are a Gen Z social media admin running a viral page for a US audience. You speak fluent internet slang, use dark humor, and understand meme culture perfectly.

            Context: A video about {video_description}.

            Your Mission: Write ONE short, punchy text overlay (Max 14 words) that reacts to this video.

            Style Guidelines:
            1. Tone: Unfiltered, relatable, slightly toxic, or hype.
            2. Vocabulary: Use current US slang.
            3. Format: POV or reaction.
            4. Must sound human.

            STRICT Safety Rules:
              - No gambling terms.

            Examples:
              - "Bro really thought he was him"
              - "My anxiety could never"
              - "Moments before disaster struck..."

            Return ONLY the text.
            """
            response = model.generate_content(prompt_overlay)
            overlay_text = response.text.strip()

        prompt_caption = f""" 
        Task: Write an Instagram caption for: {video_description}.
        Include:
        - 1 Hook
        - Hashtag #bluffinbob
        - 3 viral hashtags + 4 topic hashtags
        No gambling words.
        """
        response_caption = model.generate_content(prompt_caption)
        caption_output = response_caption.text.strip()
        
        return overlay_text, caption_output

    except Exception as e:
        return f"AI Error: {str(e)}", "AI Error" 

# ---------- FROM YOUR CODE: VIDEO PROCESSING ----------
def process_video(video_path, logo_path, user_text, video_desc, user_api_input):

    if not video_path:
        return None, None, "Upload a video first!"
        
    print("Processing video...")
    
    final_overlay, final_caption = generate_viral_content(user_api_input, video_desc, user_text)
    
    if "Error" in final_overlay:
        return None, None, final_overlay

    try:
        W, H = 1080, 1920
        background = mp.ColorClip(size=(W, H), color=(255, 255, 255))
        
        clip = mp.VideoFileClip(video_path)
        
        target_w = 980
        target_h = 1225
        
        clip_resized = clip.resize(width=target_w)
        
        if clip_resized.h > target_h:
            clip_cropped = clip_resized.crop(
                x_center=clip_resized.w/2, 
                y_center=clip_resized.h/2, 
                width=target_w, 
                height=target_h
            )
        else:
            clip_cropped = clip_resized

        clip_positioned = clip_cropped.set_position("center")
        background = background.set_duration(clip.duration)
        
        txt_clip = create_text_clip_pil(
            text=final_overlay, 
            fontsize=40, 
            color='black', 
            width=W*0.8
        )
        
        txt_clip = txt_clip.set_position(('center', 200)).set_duration(clip.duration)
        
        composite_elements = [background, clip_positioned, txt_clip]
        
        if logo_path:
            cleaned_logo_pil = remove_black_background(logo_path)
            cleaned_logo_np = np.array(cleaned_logo_pil)
            logo = mp.ImageClip(cleaned_logo_np).resize(width=250)
            logo = logo.set_position(('center', 1350))
            composite_elements.append(logo)

        final_clip = mp.CompositeVideoClip(composite_elements)
        final_clip = final_clip.set_duration(clip.duration)
        
        filename = f"output_viral_{os.urandom(4).hex()}.mp4"
        output_path = os.path.join(OUTPUT_DIR, filename)
        
        final_clip.write_videofile(output_path, fps=40, codec="libx264", audio_codec="aac")

        return filename, final_overlay, final_caption
        
    except Exception as e:
        traceback.print_exc()
        return None, None, f"Video Error: {str(e)}"

# ---------- NEW: API ENDPOINT ----------
@app.route("/api/generate-video", methods=["POST"])
def api_generate():
    temp_files = []
    try:
        video = request.files.get("video")
        if not video:
            return jsonify({"error": "Upload a video first!"}), 400

        if not SECRET_API_KEY:
            return jsonify({"error": "Server has no GEMINI_API_KEY set"}), 500

        logo = request.files.get("logo")
        desc = request.form.get("description", "")
        user_text = request.form.get("overlay_text", "")

        uid = os.urandom(4).hex()
        v_path = f"temp_video_{uid}.mp4"
        video.save(v_path)
        temp_files.append(v_path)

        l_path = None
        if logo:
            l_path = f"temp_logo_{uid}.png"
            logo.save(l_path)
            temp_files.append(l_path)

        output_filename, final_overlay, final_caption = process_video(
            v_path, l_path, user_text, desc, ""
        )

        if not output_filename:
            return jsonify({"error": final_caption or "Processing failed"}), 500

        return jsonify({
            "status": "success",
            "video_url": f"/static/output/{output_filename}",
            "overlay_text": final_overlay,
            "captions": final_caption
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    finally:
        for f in temp_files:
            if f and os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
