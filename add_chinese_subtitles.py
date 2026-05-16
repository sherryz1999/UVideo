"""
Transcribe a video with Whisper, translate subtitles to Chinese,
and burn them into a new video file using ffmpeg.
"""
import sys
import os
import json
import time
import subprocess
import whisper
from deep_translator import GoogleTranslator

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

FFMPEG_BIN_DIR = r"C:\Users\sherr\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin"
FFMPEG = os.path.join(FFMPEG_BIN_DIR, "ffmpeg.exe")

os.environ["PATH"] = FFMPEG_BIN_DIR + os.pathsep + os.environ.get("PATH", "")

MAX_CHARS_PER_BATCH = 4500


def format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments, path: str):
    with open(path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}\n")
            f.write(f"{seg['text'].strip()}\n\n")


def translate_segments(segments, target_lang="zh-CN"):
    translator = GoogleTranslator(source="auto", target=target_lang)
    translated_texts = [""] * len(segments)

    # Build batches to reduce API calls (join with \n, split after)
    batch_indices = []
    batch_texts = []
    current_batch_idx = []
    current_batch_text = []
    current_len = 0

    for i, seg in enumerate(segments):
        text = seg["text"].strip()
        if not text:
            translated_texts[i] = text
            continue
        if current_len + len(text) + 1 > MAX_CHARS_PER_BATCH and current_batch_idx:
            batch_indices.append(current_batch_idx)
            batch_texts.append(current_batch_text)
            current_batch_idx = []
            current_batch_text = []
            current_len = 0
        current_batch_idx.append(i)
        current_batch_text.append(text)
        current_len += len(text) + 1

    if current_batch_idx:
        batch_indices.append(current_batch_idx)
        batch_texts.append(current_batch_text)

    print(f"Translating {len(segments)} segments in {len(batch_texts)} batches...")

    for b, (idxs, texts) in enumerate(zip(batch_indices, batch_texts)):
        joined = "\n".join(texts)
        for attempt in range(3):
            try:
                result = translator.translate(joined)
                break
            except Exception as e:
                if attempt == 2:
                    print(f"  [warn] batch {b} failed after 3 tries: {e}")
                    result = joined
                else:
                    time.sleep(1)

        parts = result.split("\n")
        # If Google merges/splits lines unexpectedly, fall back to original count
        if len(parts) != len(texts):
            parts = parts[:len(texts)] + texts[len(parts):]

        for idx, zh in zip(idxs, parts):
            translated_texts[idx] = zh.strip()

        if (b + 1) % 10 == 0 or b == len(batch_texts) - 1:
            print(f"  batch {b+1}/{len(batch_texts)} done")

    return [{**seg, "text": translated_texts[i]} for i, seg in enumerate(segments)]


def burn_subtitles(video_in: str, srt_path: str, video_out: str):
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")
    cmd = [
        FFMPEG, "-y",
        "-i", video_in,
        "-vf", (
            f"subtitles='{srt_escaped}'"
            ":force_style='FontName=Arial Unicode MS,FontSize=22,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Alignment=2'"
        ),
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "copy",
        video_out,
    ]
    print(f"\nRunning ffmpeg to burn subtitles...")
    subprocess.run(cmd, check=True)


def main():
    if len(sys.argv) < 2:
        print("Usage: python add_chinese_subtitles.py <video_file> [whisper_model]")
        print("  whisper_model: tiny, base, small, medium, large  (default: base)")
        sys.exit(1)

    video_in = sys.argv[1]
    model_name = sys.argv[2] if len(sys.argv) > 2 else "base"

    if not os.path.exists(video_in):
        print(f"Error: file not found: {video_in}")
        sys.exit(1)

    base_name = os.path.splitext(video_in)[0]
    transcript_cache = base_name + "_transcript.json"
    srt_path = base_name + "_zh.srt"
    video_out = base_name + "_zh.mp4"

    if os.path.exists(transcript_cache):
        print(f"=== Loading cached transcript from {transcript_cache} ===")
        with open(transcript_cache, encoding="utf-8") as f:
            segments = json.load(f)
        print(f"Loaded {len(segments)} segments.")
    else:
        print(f"=== Step 1: Transcribing with Whisper ({model_name} model) ===")
        model = whisper.load_model(model_name)
        result = model.transcribe(video_in, verbose=False)
        segments = result["segments"]
        print(f"Detected language: {result.get('language')}, segments: {len(segments)}")
        with open(transcript_cache, "w", encoding="utf-8") as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)
        print(f"Transcript cached -> {transcript_cache}")

    print(f"\n=== Step 2: Translating {len(segments)} segments to Chinese ===")
    zh_segments = translate_segments(segments)

    print(f"\n=== Step 3: Writing SRT -> {srt_path} ===")
    write_srt(zh_segments, srt_path)
    print("SRT written.")

    print(f"\n=== Step 4: Burning subtitles -> {video_out} ===")
    burn_subtitles(video_in, srt_path, video_out)

    print(f"\nDone!  Output: {video_out}")
    print(f"Subtitle file: {srt_path}")


if __name__ == "__main__":
    main()
