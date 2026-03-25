from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import os

from typing import Any

app = FastAPI(title="YouTube Downloader API")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_PATH = os.path.join(BASE_DIR, "cookies.txt")

# Enable CORS for frontend interaction
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Common yt-dlp options to bypass bot detection
YDL_OPTS_BASE: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "ios", "web"]
        }
    },
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
    },
}

# Force absolute path for cookies to guarantee Railway Docker container finds them
if os.path.exists(COOKIE_PATH):
    YDL_OPTS_BASE["cookiefile"] = COOKIE_PATH


def search_via_ytdlp(q: str):
    opts = {
        **YDL_OPTS_BASE,
        "extract_flat": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch50:{q}", download=False)
        results = []
        for entry in info.get("entries", []):
            if not entry:
                continue
            results.append({
                "id": entry.get("id"),
                "url": f"https://www.youtube.com/watch?v={entry.get('id')}",
                "title": entry.get("title"),
                "author": entry.get("uploader") or entry.get("channel"),
                "thumbnail_url": entry.get("thumbnail") or f"https://i.ytimg.com/vi/{entry.get('id')}/hqdefault.jpg",
                "length": entry.get("duration"),
                "views": entry.get("view_count"),
            })
        return results


def search_via_pytubefix(q: str):
    from pytubefix import Search
    s = Search(q)
    results = []
    for video in s.videos[:50]:
        results.append({
            "id": video.video_id,
            "url": video.watch_url,
            "title": video.title,
            "author": video.author,
            "thumbnail_url": video.thumbnail_url,
            "length": video.length,
            "views": video.views,
        })
    return results


def info_via_ytdlp(url: str):
    opts = {
        **YDL_OPTS_BASE,
        "skip_download": True,
        "format": "all",  # Prevent "Requested format is not available"
        "ignoreerrors": True, # Ignore format selection errors
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats_list = []

    for f in info.get("formats", []):
        vcodec = str(f.get("vcodec", "none")).lower()
        acodec = str(f.get("acodec", "none")).lower()
        ext = f.get("ext", "mp4")
        height = f.get("height") or 0
        filesize = f.get("filesize") or f.get("filesize_approx") or 0

        has_video = vcodec not in ("none", "false", "")
        has_audio = acodec not in ("none", "false", "")

        # Progressive video (video+audio combined) up to 720p
        if has_video and has_audio and height <= 720 and height > 0:
            formats_list.append({
                "url": f.get("url"),
                "resolution": f"{height}p",
                "filesize": round(filesize / (1024 * 1024), 2) if filesize else None,
                "mime_type": f"video/{ext}",
                "type": "video",
            })
        # Best audio-only
        elif not has_video and has_audio:
            formats_list.append({
                "url": f.get("url"),
                "resolution": f"{f.get('abr', 'N/A')} kbps Audio",
                "filesize": round(filesize / (1024 * 1024), 2) if filesize else None,
                "mime_type": f"audio/{ext}",
                "type": "audio",
            })

    # Fallback: if we filtered out everything (e.g., DASH-only video), just return everything we have
    if not formats_list and info.get("formats"):
        for f in info.get("formats", []):
            url = f.get("url", "")
            if not url or "sb/" in url or f.get("format_note") == "storyboard":
                continue
            fs = f.get("filesize") or f.get("filesize_approx") or 0
            fs_val = float(fs) / (1024 * 1024) if fs else 0
            formats_list.append({
                "url": f.get("url"),
                "resolution": f.get("format_note") or f.get("resolution") or (f"{f.get('height')}p" if f.get("height") else "Audio/Unknown"),
                "filesize": round(fs_val, 2) if fs_val > 0 else None,
                "mime_type": f"video/{f.get('ext', 'mp4')}" if str(f.get("vcodec", "none")).lower() != "none" else f"audio/{f.get('ext', 'm4a')}",
                "type": "video" if str(f.get("vcodec", "none")).lower() != "none" else "audio",
            })

    # Do not inject debug formats here, let it return empty so the router can fall back to pytubefix
    seen = {}
    for fmt in formats_list:
        key = (fmt["resolution"], fmt["type"])
        if key not in seen:
            seen[key] = fmt
    formats_list = sorted(seen.values(), key=lambda x: (x["type"], x["resolution"]), reverse=True)

    description = info.get("description") or ""
    return {
        "title": info.get("title"),
        "author": info.get("uploader") or info.get("channel"),
        "length": info.get("duration"),
        "thumbnail_url": info.get("thumbnail") or f"https://i.ytimg.com/vi/{str(url).split('v=')[-1].split('&')[0]}/maxresdefault.jpg",
        "views": info.get("view_count"),
        "description": (description[:200] + "...") if len(description) > 200 else description,
        "formats": formats_list,
    }


def info_via_pytubefix(url: str):
    from pytubefix import YouTube
    # ANDROID_VR client natively bypasses YouTube VPS Bot detection (429) without needing cookies
    yt = YouTube(url, use_oauth=False, allow_oauth_cache=True, client="ANDROID_VR")

    formats_list = []
    streams = yt.streams.filter(progressive=True, file_extension="mp4").order_by("resolution").desc()
    for stream in streams:
        try:
            res_str = stream.resolution or "0p"
            res_val = int(res_str.replace("p", ""))
            if res_val <= 720:
                formats_list.append({
                    "url": stream.url,
                    "resolution": stream.resolution,
                    "filesize": round(stream.filesize / (1024 * 1024), 2),
                    "mime_type": stream.mime_type,
                    "type": "video",
                })
        except (ValueError, AttributeError):
            continue

    audio_stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
    if audio_stream:
        formats_list.append({
            "url": audio_stream.url,
            "resolution": f"{audio_stream.abr} Audio",
            "filesize": round(audio_stream.filesize / (1024 * 1024), 2),
            "mime_type": audio_stream.mime_type,
            "type": "audio",
        })

    description = yt.description or ""
    return {
        "title": yt.title,
        "author": yt.author,
        "length": yt.length,
        "thumbnail_url": yt.thumbnail_url,
        "views": yt.views,
        "description": (description[:200] + "...") if len(description) > 200 else description,
        "formats": formats_list,
    }


@app.get("/api/search")
async def search_videos(q: str = Query(..., description="Search query")):
    try:
        return search_via_ytdlp(q)
    except Exception as e_ytdlp:
        try:
            return search_via_pytubefix(q)
        except Exception as e_pytubefix:
            raise HTTPException(
                status_code=400,
                detail=f"yt-dlp: {e_ytdlp} | pytubefix: {e_pytubefix}"
            )


@app.get("/api/info")
async def get_video_info(url: str = Query(..., description="YouTube Video URL")):
    debug_log = []
    
    # 1. yt-dlp 
    try:
        data = info_via_ytdlp(url)
        debug_log.append("ytdlp_cookies: " + ("FOUND" if os.path.exists(COOKIE_PATH) else "MISSING"))
        if not data.get("formats"): raise Exception("yt-dlp returned no valid formats (only storyboards or blocked)")
        return data
    except Exception as e_ytdlp:
        debug_log.append(f"yt-dlp_err: {str(e_ytdlp)}")
        
        # 2. pytubefix with ANDROID_VR client
        try:
            data = info_via_pytubefix(url)
            if not data.get("formats"): raise Exception("pytubefix returned no formats")
            return data
        except Exception as e_pytubefix:
            debug_log.append(f"pytubefix_err: {str(e_pytubefix)}")
            
            # 3. Ultimate Proxy logic (Moved out of info_via_ytdlp)
            try:
                import urllib.request, json
                video_id = url.split("v=")[-1].split("&")[0]
                instances = ["https://pipedapi.kavin.rocks", "https://pipedapi.in.projectsegfau.lt", "https://vid.puffyan.us"]
                formats_list = []
                for instance in instances:
                    is_piped = "piped" in instance
                    endpoint = f"{instance}/streams/{video_id}" if is_piped else f"{instance}/api/v1/videos/{video_id}"
                    try:
                        req = urllib.request.Request(endpoint, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=5) as response:
                            p_data = json.loads(response.read().decode())
                        
                        if is_piped:
                            for stream in p_data.get("videoStreams", []):
                                if not stream.get("videoOnly"):
                                    formats_list.append({"url": stream.get("url"), "resolution": stream.get("quality"), "type": "video", "mime_type": "video/mp4", "filesize": None})
                            for stream in p_data.get("audioStreams", []):
                                formats_list.append({"url": stream.get("url"), "resolution": "Audio", "type": "audio", "mime_type": "audio/m4a", "filesize": None})
                        else:
                            for f in p_data.get("formatStreams", []):
                                formats_list.append({"url": f.get("url"), "resolution": f.get("resolution"), "type": "video", "mime_type": "video/mp4", "filesize": None})
                        
                        if formats_list:
                            break
                    except Exception as ex:
                        debug_log.append(f"proxy_{instance.replace('https://', '')}_err: {str(ex)}")
                        continue

                if formats_list:
                    return {
                        "title": "YouTube Video (Proxy Fallback)",
                        "author": "Unknown",
                        "length": 0,
                        "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
                        "views": 0,
                        "description": "Information fetched via proxy because Railway IP is blocked.",
                        "formats": formats_list
                    }
            except Exception as e_proxy:
                debug_log.append(f"proxy_fatal: {str(e_proxy)}")

            return {
                "title": "Blocked by YouTube on Railway",
                "author": "Backend Error",
                "length": 0,
                "thumbnail_url": "",
                "views": 0,
                "description": "YouTube blocks Railway IPs. Proxies failed.",
                "formats": [
                    {
                        "url": "ERROR_LOG",
                        "resolution": "Debug Info",
                        "filesize": None,
                        "mime_type": "text/plain",
                        "type": "debug",
                        "debug_txt": " | ".join(debug_log)
                    }
                ]
            }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
