import os
import textwrap
import traceback
import numpy as np
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import moviepy.editor as mp
import google.generativeai as genai
from PIL import Image, ImageDraw, ImageFont

# ==========================================
#  FIX: MONKEY PATCH FOR PILLOW 10 ERROR
# ==========================================
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.LANCZOS
# ==========================================

# ---------- FLASK APP SETUP (Connection to HTML) ----------
app = Flask(__name__)
CORS(app)

# ---------- CONFIG ----------
OUTPUT_DIR = "static/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)
SECRET_API_KEY = os.getenv("GEMINI_API_KEY")

# ---------- ROUTES (Connection to HTML) ----------
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/static/output/<path:filename>")
def serve_output(filename):
    return send_from_directory(OUTPUT_DIR, filename)

# =========================================================
#  YOUR LOGIC FROM APP.PY STARTS HERE
#  (Preserved: Prompts, Resize, Positions, Font Sizes)
# =========================================================

def remove_black_background(image_path):
    # Open the image
    image = Image.open(image_path).convert("RGBA")
    datas = image.getdata()
    
    new_data = []
    for item in datas:
        # If the pixel is black (or close to black), make it transparent
        if item[:3] == (0, 0, 0):  # pure black
            new_data.append((item[0], item[1], item[2], 0))  # fully transparent
        else:
            new_data.append(item)
    
    image.putdata(new_data)
    return image

def create_text_clip_pil(text, fontsize=50, color='black', width=1000, font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
    """
    Creates a text image using Pillow instead of ImageMagick.
    Returns a MoviePy ImageClip.
    """
    # 1. Setup Font
    try:
        font = ImageFont.truetype(font_path, fontsize)
    except IOError:
        # Fallback to default if specific font not found
        font = ImageFont.load_default()
        print("Warning: Custom font not found, using default.")

    # 2. Calculate Text Size
    dummy_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    
    # Text wrapping
    lines = textwrap.wrap(text, width=32) # Wrap text to fit width
    final_text = "\n".join(lines)
    
    # Calculate bounding box
    bbox = dummy_draw.multiline_textbbox((0, 0), final_text, font=font, align="center")
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    # Add some padding
    img_w = int(text_width + 100)
    img_h = int(text_height + 50)
    
    # 3. Draw Text on Transparent Background
    img = Image.new('RGBA', (img_w, img_h), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    
    # Draw text centered
    draw.multiline_text(
        (img_w/2, img_h/2), 
        final_text, 
        font=font, 
        fill=color, 
        anchor="mm", 
        align="center"
    )
    
    # 4. Convert to MoviePy Clip
    numpy_img = np.array(img)
    return mp.ImageClip(numpy_img)

def generate_viral_content(user_api_key, video_description, manual_text):
    api_key = SECRET_API_KEY if SECRET_API_KEY else user_api_key
    
    if not api_key:
        return "Error: No API Key.", "Error: No API Key."

    try:
        genai.configure(api_key=api_key)
        
        # Try requested model, fallback if needed
        try:
            model = genai.GenerativeModel('gemini-2.5-flash')
        except:
            model = genai.GenerativeModel('gemini-1.5-flash')

        # 1. Overlay Text
        if manual_text:
            overlay_text = manual_text
        else:
            prompt_overlay = f"""
            Role: You are a Gen Z social media admin running a viral page for a US audience. You speak fluent internet slang, use dark humor, and understand meme culture perfectly.

            Context: A video about {video_description}.

            Your Mission: Write ONE short, punchy text overlay (Max 14 words) that reacts to this video.

            Style Guidelines:
            1. Tone: Unfiltered, relatable, slightly toxic, or hype.
            2. Vocabulary: Use current US slang (e.g., "bro," "cooked," "wild," "real,").
            3. Format: Can be a POV, a reaction, or a caption.
            4. Vibe Check: It must sound like a human wrote it, not a robot.

            STRICT Safety Rules:
              - ABSOLUTELY NO gambling terms (Big Win, Casino, Bet, Jackpot).
              - If the video involves luck/money, focus on the "shock" or "reaction," not the gambling aspect.

            Examples of the Vibe I want:
              - "Bro really thought he was him "
              - "My anxiety could never "
              - "Who allowed this man outside??"
              - "Moments before disaster struck..."

            Task: Based on the context above, write the text overlay now.
            Return ONLY the text. No quotes. 
            """
            response = model.generate_content(prompt_overlay)
            overlay_text = response.text.strip()  

        # 2. Caption
        prompt_caption = f""" 
        Task: Write an Instagram caption for: {video_description}.
        Include:
        - 1 Hook sentence.
        - Hashtag #bluffinbob
        - 3 Viral hashtags , 4 video topic viral hashtags
        Constraint: No gambling words.                               
        """
        response_caption = model.generate_content(prompt_caption)
        caption_output = response_caption.text.strip()
        
        return overlay_text, caption_output
    except Exception as e:
        return f"AI Error: {str(e)}", "AI Error" 

# --- VIDEO PROCESSING (Your Logic) ---
def process_video_logic(video_path, logo_path, user_text, video_desc, user_api_input):
    if not video_path:
        return None, None, "Upload a video first!"
        
    print("Processing video...")
    
    # Get Text from AI
    final_overlay, final_caption = generate_viral_content(user_api_input, video_desc, user_text)
    
    if "Error" in final_overlay:
        return None, None, final_overlay

    try:
        # Settings
        W, H = 1080, 1920
        background = mp.ColorClip(size=(W, H), color=(255, 255, 255))
        
        # Load Video
        clip = mp.VideoFileClip(video_path)
        
        # --- OPTIMIZATION: SAFETY CLIP ---
        # Free servers crash on videos > 60s. We clip it to be safe.
        if clip.duration > 60:
            print("Video too long for free tier, clipping to 60s")
            clip = clip.subclip(0, 60)
        
        # Crop & Resize (4:5 Ratio)
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
        
        # --- NEW TEXT METHOD (The Fix) ---
        # Instead of mp.TextClip, we use our PIL function
        txt_clip = create_text_clip_pil(
            text=final_overlay, 
            fontsize=43,   # Your specified fontsize
            color='black', 
            width=W*0.9
        )
        
        # Position Text Top Center (180px down) -> Your code said 200 actually
        txt_clip = txt_clip.set_position(('center', 200)).set_duration(clip.duration)
        
        composite_elements = [background, clip_positioned, txt_clip]

        # Logo
        if logo_path:
            logo = mp.ImageClip(logo_path).resize(width=150)
            logo = logo.set_position(('center', 1400)) # Your specific position
            composite_elements.append(logo)

        # Render
        final_clip = mp.CompositeVideoClip(composite_elements)
        final_clip = final_clip.set_duration(clip.duration)
        
        # Generate Random Filename for web serving (HTML requirement)
        filename = f"output_viral_{os.urandom(4).hex()}.mp4"
        output_path = os.path.join(OUTPUT_DIR, filename)
        
        # *** OPTIMIZATION APPLIED HERE ***
        # preset='ultrafast' + threads=4 is the fastest possible setting.
        # fps=30 saves 25% memory/time compared to 40.
        final_clip.write_videofile(
            output_path, 
            fps=30, 
            codec="libx264", 
            audio_codec="aac",
            preset="ultrafast",
            threads=4
        )
        
        return filename, final_overlay, final_caption
        
    except Exception as e:
        traceback.print_exc()
        return None, None, f"Video Error: {str(e)}"

# =========================================================
#  API LOGIC (Connecting your logic to HTML)
# =========================================================

@app.route("/api/generate-video", methods=["POST"])
def api_generate():
    temp_files = []
    try:
        video = request.files.get("video")
        if not video:
            return jsonify({"error": "Upload a video first!"}), 400

        logo = request.files.get("logo")
        desc = request.form.get("description", "")
        user_text = request.form.get("overlay_text", "")

        # Save temporary files
        uid = os.urandom(4).hex()
        v_path = f"temp_video_{uid}.mp4"
        video.save(v_path)
        temp_files.append(v_path)

        l_path = None
        if logo:
            l_path = f"temp_logo_{uid}.png"
            logo.save(l_path)
            temp_files.append(l_path)

        # Call YOUR processing logic
        output_filename, final_overlay, final_caption = process_video_logic(
            v_path, l_path, user_text, desc, "" 
        )

        if not output_filename:
            return jsonify({"error": final_caption or "Processing failed"}), 500

        # Return result to HTML
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
        # Cleanup temp upload files
        for f in temp_files:
            if f and os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
