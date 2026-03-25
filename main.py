from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pytubefix import YouTube, Search
from pytubefix.cli import on_progress
import os

app = FastAPI(title="YouTube Downloader API")

# Enable CORS for frontend interaction
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/search")
async def search_videos(q: str = Query(..., description="Search query")):
    try:
        s = Search(q)
        results = []
        for video in s.videos[:10]: # Limit to 10 results
            results.append({
                "id": video.video_id,
                "url": video.watch_url,
                "title": video.title,
                "author": video.author,
                "thumbnail_url": video.thumbnail_url,
                "length": video.length,
                "views": video.views
            })
        return results
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/info")
async def get_video_info(url: str = Query(..., description="YouTube Video URL")):
    try:
        # Using ANDROID client and oauth to bypass sign-in issues
        yt = YouTube(url, use_oauth=False, allow_oauth_cache=True)
        
        # Get video details
        formats_list = []
        
        # Filter progressive streams (video + audio) up to 720p
        streams = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc()
        
        for stream in streams:
            try:
                res_str = stream.resolution or "0p"
                res_val = int(res_str.replace('p', ''))
                if res_val <= 720:
                    formats_list.append({
                        "url": stream.url,
                        "resolution": stream.resolution,
                        "filesize": round(stream.filesize / (1024 * 1024), 2),
                        "mime_type": stream.mime_type,
                        "type": "video"
                    })
            except (ValueError, AttributeError):
                continue
        
        # Add best audio-only stream
        audio_stream = yt.streams.filter(only_audio=True).order_by('abr').desc().first()
        if audio_stream:
            formats_list.append({
                "url": audio_stream.url,
                "resolution": f"{audio_stream.abr} Audio",
                "filesize": round(audio_stream.filesize / (1024 * 1024), 2),
                "mime_type": audio_stream.mime_type,
                "type": "audio"
            })
        
        video_details = {
            "title": yt.title,
            "author": yt.author,
            "length": yt.length,
            "thumbnail_url": yt.thumbnail_url,
            "views": yt.views,
            "description": (yt.description[:200] + "...") if yt.description else "",
            "formats": formats_list
        }
        
        return video_details
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
