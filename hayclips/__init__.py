"""HayClips: local operator pipeline that turns long Armenian conversations into reviewable vertical clips.

Package layout (Phase 1a):
  config        settings, cost limits, tool paths (env-driven)
  errors        typed pipeline errors with operator hints
  jsonio        atomic JSON persistence
  proc          the only place that starts external processes (arg lists, timeouts, cleanup)
  models        domain records (project, clip, window, consent, transcription attempt)
  project       file-based project repository: who owns which state file
  sources       SourceProvider interface + YouTubeSource (yt-dlp)
  selection     caption parsing, sentence units, explainable candidate scoring
  transcription paid provider integration (Harmar), durable attempts, fake server for tests
  media         ffprobe/ffmpeg helpers, alignment check, speaker reframing
  captions      sentence snapping, ASS caption styles, font preflight
  render        per-clip render orchestration and review page
"""
