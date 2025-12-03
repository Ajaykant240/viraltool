import os
import traceback
import textwrap
import numpy as np
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import moviepy.editor as mp
import google.generativeai as genai
from PIL import Image, ImageDraw, ImageFont

# Initialize Flask App
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes to prevent browser connection errors

# Configuration
OUTPUT_DIR = "static/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ===========================
#  HELPER: SERVE FRONTEND
# ===========================
@app.route("/")
def index():
    # Serves the index.html file from the root directory
    return send_from_directory('.', 'index.html')

@app.route("/static/output/<path:filename>")
def serve_output(filename):
    return send_from_directory(OUTPUT_DIR, filename)

# ===========================
#  HELPER: IMAGE PROCESSING
# ===========================
def remove_black_background(image_path):
    """
    Removes black background from logos/images to make them transparent.
    """
    try:
        image = Image.open(image_path).convert("RGBA")
        datas = image.getdata()
        new_data = []

        for item in datas:
            # Check for black pixels (low RGB values)
            if item[0] < 50 and item[1] < 50 and item[2] < 50:
                new_data.append((255, 255, 255, 0))  # Transparent
            else:
                new_data.append(item)

        image.putdata(new_data)
        return image
    except Exception as e:
        print(f"Error removing background: {e}")
        return Image.open(image_path).convert("RGBA")

def create_text_clip_pil(text, fontsize=50, color="black", font_path=None):
    """
    Creates a text image using Pillow (PIL) instead of MoviePy's TextClip.
    This avoids ImageMagick dependency errors on Render.
    """
    # 1. Load Font
    try:
        # Try loading a standard font, fallback to default if fails
        font = ImageFont.truetype("arial.ttf", fontsize)
    except IOError:
        try:
            # Linux path often found on Render/servers
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", fontsize)
        except:
            font = ImageFont.load_default()

    # 2. Wrap Text
    lines = textwrap.wrap(text, width=25) # Adjust width for tighter/wider text
    final_text = "\n".join(lines)

    # 3. Calculate Dimensions
    dummy_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox = dummy_draw.multiline_textbbox((0, 0), final_text, font=font, align="center")
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    # Add padding
    img_w = int(text_width + 60)
    img_h = int(text_height + 40)

    # 4. Draw Text
    img = Image.new("RGBA", (img_w, img_h), (255, 255, 255, 0))
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

    # 5. Convert to MoviePy Clip
    return mp.ImageClip(np.array(img))

# ===========================
#  LOGIC: AI TEXT GENERATION
# ===========================
def generate_AI_text(api_key, video_desc, manual_text):
    genai.configure(api_key=api_key)

    try:
        # Attempt to use the newer model, fallback to 1.5 if needed
        model = genai.GenerativeModel("gemini-1.5-flash")
    except:
        model = genai.GenerativeModel("gemini-pro")

    # 1. Overlay Text
    if manual_text and manual_text.strip():
        overlay_text = manual_text
    else:
        prompt_overlay = f"""
        Write a funny, viral, short POV text overlay (max 10 words) for a video about: "{video_desc}".
        Style: Gen Z, Meme, TikTok. 
        Return ONLY the text. No quotes.
        """
        try:
            if not video_desc: video_desc = "random funny moment"
            overlay_text = model.generate_content(prompt_overlay).text.strip().replace('"', '')
        except Exception as e:
            print(f"AI Error (Overlay): {e}")
            overlay_text = "Wait for it..."

    # 2. Caption
    prompt_caption = f"""
    Write an engaging Instagram caption for this video: "{video_desc}".
    Include:
    - A hook in the first line.
    - The hashtag #viral
    - 3 relevant hashtags.
    """
    try:
        caption = model.generate_content(prompt_caption).text.strip()
    except Exception as e:
        print(f"AI Error (Caption): {e}")
        caption = f"Check this out! #viral #reels {video_desc}"

    return overlay_text, caption

# ===========================
#  LOGIC: VIDEO EDITING
# ===========================
def process_video(video_path, logo_path, music_path, overlay_text, volume_level):
    
    # Load Video
    clip = mp.VideoFileClip(video_path)
    
    # Canvas Settings (9:16 aspect ratio)
    CANVAS_W, CANVAS_H = 1080, 1920
    
    # 1. Create White Background
    bg = mp.ColorClip(size=(CANVAS_W, CANVAS_H), color=(255, 255, 255), duration=clip.duration)

    # 2. Resize & Center Original Video
    # We leave some margin on sides (width=980 instead of 1080)
    video_clip = clip.resize(width=980)
    
    # If video is too tall (taller than space available minus text area), crop center
    max_h = 1300
    if video_clip.h > max_h:
        video_clip = video_clip.crop(x_center=video_clip.w/2, y_center=video_clip.h/2, width=980, height=max_h)
    
    video_clip = video_clip.set_position("center")

    # 3. Create Text Overlay
    # Font size scales with text length roughly
    f_size = 65 if len(overlay_text) < 20 else 50
    txt_clip = create_text_clip_pil(overlay_text, fontsize=f_size, color="black")
    
    # Position text above the video (y=200 is a safe area below Instagram UI elements)
    txt_clip = txt_clip.set_position(("center", 250)).set_duration(clip.duration)

    # 4. Handle Logo (Optional)
    final_layers = [bg, video_clip, txt_clip]
    
    if logo_path:
        try:
            # Process logo to remove black background and resize
            logo_img = remove_black_background(logo_path)
            logo_clip = mp.ImageClip(np.array(logo_img)).resize(width=200)
            
            # Position logo at bottom
            logo_clip = logo_clip.set_position(("center", 1450)).set_duration(clip.duration)
            final_layers.append(logo_clip)
        except Exception as e:
            print(f"Logo processing failed: {e}")

    # 5. Composite Video
    final_video = mp.CompositeVideoClip(final_layers)

    # 6. Audio Mixing
    original_audio = clip.audio if clip.audio else None
    
    if music_path:
        music_clip = mp.AudioFileClip(music_path)
        
        # Loop music if it's shorter than video
        if music_clip.duration < clip.duration:
            music_clip = mp.afx.audio_loop(music_clip, duration=clip.duration)
        else:
            music_clip = music_clip.subclip(0, clip.duration)
            
        # Adjust volumes
        music_vol = float(volume_level) / 100.0
        music_clip = music_clip.volumex(music_vol)
        
        if original_audio:
            # Mix original audio (slightly lower) with music
            final_audio = mp.CompositeAudioClip([original_audio.volumex(0.8), music_clip])
        else:
            final_audio = music_clip
            
        final_video = final_video.set_audio(final_audio)
    else:
        # Keep original audio if no music added
        if original_audio:
            final_video = final_video.set_audio(original_audio)

    # 7. Write Output
    output_filename = f"viral_{os.urandom(4).hex()}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    
    # Use 'ultrafast' preset for speed on free servers
    # 'threads' parameter helps utilize CPU cores
    final_video.write_videofile(
        output_path, 
        fps=30, 
        codec="libx264", 
        audio_codec="aac", 
        preset="ultrafast",
        threads=4
    )

    return output_path

# ===========================
#  API ROUTE: GENERATE
# ===========================
@app.route("/api/generate-video", methods=["POST"])
def api_generate():
    temp_files = []
    try:
        # Get Data
        video = request.files.get("video")
        api_key = request.form.get("api_key")
        
        if not video or not api_key:
            return jsonify({"error": "Video and API Key are required"}), 400

        logo = request.files.get("logo")
        music = request.files.get("music")
        
        desc = request.form.get("description", "")
        overlay_text_input = request.form.get("overlay_text", "")
        volume = request.form.get("music_volume", "60")

        # Save Inputs Temporarily
        unique_id = os.urandom(4).hex()
        
        v_path = f"temp_vid_{unique_id}.mp4"
        video.save(v_path)
        temp_files.append(v_path)

        l_path = None
        if logo:
            l_path = f"temp_logo_{unique_id}.png"
            logo.save(l_path)
            temp_files.append(l_path)

        m_path = None
        if music:
            m_path = f"temp_music_{unique_id}.mp3"
            music.save(m_path)
            temp_files.append(m_path)

        # 1. Generate Text AI
        final_overlay, final_caption = generate_AI_text(api_key, desc, overlay_text_input)

        # 2. Process Video
        output_file = process_video(v_path, l_path, m_path, final_overlay, volume)

        # Response
        return jsonify({
            "status": "success",
            "video_url": f"/{output_file}",
            "overlay_text": final_overlay,
            "captions": final_caption
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
        
    finally:
        # Clean up temp files to save space
        for f in temp_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass

if __name__ == "__main__":
    # Debug mode is false for production
    app.run(host="0.0.0.0", port=5000, debug=False)
