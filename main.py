from flask import Flask, request, jsonify, send_from_directory
import os
import moviepy.editor as mp
import google.generativeai as genai
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import textwrap
import traceback

app = Flask(__name__)

# Output folder for Render
OUTPUT_DIR = "static/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ===========================
#  REMOVE BLACK BACKGROUND
# ===========================
def remove_black_background(image_path):
    image = Image.open(image_path).convert("RGBA")
    datas = image.getdata()
    new_data = []

    for item in datas:
        if sum(item[:3]) < 50:
            new_data.append((item[0], item[1], item[2], 0))
        else:
            new_data.append(item)

    image.putdata(new_data)
    return image

# ===========================
#  PIL TEXT CLIP
# ===========================
def create_text_clip_pil(text, fontsize=40, color="black", width=1000,
                         font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):

    try:
        font = ImageFont.truetype(font_path, fontsize)
    except:
        font = ImageFont.load_default()

    lines = textwrap.wrap(text, width=32)
    final_text = "\n".join(lines)

    dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox = dummy.multiline_textbbox((0, 0), final_text, font=font, align="center")
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]

    img_w = int(w + 100)
    img_h = int(h + 50)

    img = Image.new("RGBA", (img_w, img_h), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    draw.multiline_text((img_w/2, img_h/2), final_text, font=font, fill=color,
                        anchor="mm", align="center")

    return mp.ImageClip(np.array(img))

# ===========================
#  GEMINI AI LOGIC
# ===========================
def generate_AI_text(api_key, video_desc, manual_text):
    genai.configure(api_key=api_key)

    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
    except:
        model = genai.GenerativeModel("gemini-1.5-flash")

    # Overlay Text
    if manual_text:
        overlay_text = manual_text
    else:
        prompt = f"""
        Create a viral US Gen-Z style overlay text (max 14 words) for a video about: {video_desc}.
        No gambling words.
        Return only the text.
        """
        overlay_text = model.generate_content(prompt).text.strip()

    # Caption
    caption_prompt = f"""
    Write an Instagram caption for: {video_desc}
    Requirements:
    - 1 strong hook
    - Hashtag #bluffinbob
    - 3 viral hashtags + 4 topic hashtags
    """
    caption = model.generate_content(caption_prompt).text.strip()

    return overlay_text, caption

# ===========================
#  VIDEO PROCESSING
# ===========================
def generate_video(video_file, logo_file, text, desc, api_key, music_file, music_volume):
    overlay_text, caption = generate_AI_text(api_key, desc, text)

    clip = mp.VideoFileClip(video_file)
    W, H = 1080, 1920
    bg = mp.ColorClip((W, H), color=(255, 255, 255)).set_duration(clip.duration)

    resized = clip.resize(width=980)

    if resized.h > 1225:
        cropped = resized.crop(
            x_center=resized.w/2,
            y_center=resized.h/2,
            width=980, height=1225
        )
    else:
        cropped = resized

    base = cropped.set_position("center")

    txt = create_text_clip_pil(overlay_text, fontsize=40)
    txt = txt.set_duration(clip.duration).set_position(("center", 200))

    elements = [bg, base, txt]

    # LOGO
    if logo_file:
        clean = remove_black_background(logo_file)
        clean_np = np.array(clean)
        logo = mp.ImageClip(clean_np).resize(width=250)
        logo = logo.set_position(("center", 1350)).set_duration(clip.duration)
        elements.append(logo)

    # MUSIC
    if music_file:
        music = mp.AudioFileClip(music_file).volumex(float(music_volume) / 100)
        final_audio = mp.CompositeAudioClip([clip.audio, music])
    else:
        final_audio = clip.audio

    video = mp.CompositeVideoClip(elements)
    video = video.set_audio(final_audio)

    output_path = os.path.join(OUTPUT_DIR, "final_output.mp4")
    video.write_videofile(output_path, fps=40, codec="libx264", audio_codec="aac")

    return output_path, overlay_text, caption

# ===========================
#  API ROUTE
# ===========================
@app.route("/api/generate-video", methods=["POST"])
def api_generate():
    try:
        video = request.files.get("video")
        logo = request.files.get("logo")
        music = request.files.get("music")

        text = request.form.get("overlay_text", "")
        desc = request.form.get("description", "")
        api_key = request.form.get("api_key", "")
        volume = request.form.get("music_volume", "60")

        if not video or not api_key:
            return jsonify({"error": "Missing video or API key"}), 400

        # Save temp files
        v_path = "temp_video.mp4"
        video.save(v_path)

        l_path = None
        if logo:
            l_path = "temp_logo.png"
            logo.save(l_path)

        m_path = None
        if music:
            m_path = "temp_music.mp3"
            music.save(m_path)

        out_path, final_text, final_captions = generate_video(
            v_path, l_path, text, desc, api_key, m_path, volume
        )

        return jsonify({
            "video_url": f"/{out_path}",
            "overlay_text": final_text,
            "captions": final_captions
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/static/output/<path:filename>")
def serve_output(filename):
    return send_from_directory("static/output", filename)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
