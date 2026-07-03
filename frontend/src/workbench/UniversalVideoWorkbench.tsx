import {useEffect, useMemo, useRef, useState} from 'react';
import 'media-chrome';
import {
  mediaApi,
  type MediaMetadata,
  type VideoSegment,
} from '../api';
import {workspacePathLabel} from '../studio/labels';

export type SegmentPayload = {
  source_path: string;
  start_sec: number;
  end_sec: number;
  duration_sec: number;
  label?: string | null;
  notes?: string | null;
};

export type UniversalVideoWorkbenchProps = {
  workspacePath: string;
  title?: string;
  mode?: 'viewer' | 'marking' | 'preview';
  initialInSec?: number | null;
  initialOutSec?: number | null;
  onUseAsSource?: (path: string) => void;
  onSendToCut?: (payload: SegmentPayload) => void;
  onSendToRender?: (payload: SegmentPayload) => void;
};

type FitMode = 'contain' | 'cover' | 'original' | 'vertical';

const fmtTime = (seconds?: number | null): string => {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return '00:00';
  const value = Math.max(0, Number(seconds));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const rest = Math.floor(value % 60);
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
    : `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
};

const fmtBytes = (bytes?: number | null): string => {
  if (!bytes) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = Number(bytes);
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
};

const selectedPayload = (
  sourcePath: string,
  inSec: number | null,
  outSec: number | null,
  label?: string,
  notes?: string,
): SegmentPayload | null => {
  if (inSec === null || outSec === null || outSec <= inSec) return null;
  return {
    source_path: sourcePath,
    start_sec: inSec,
    end_sec: outSec,
    duration_sec: outSec - inSec,
    label: label || null,
    notes: notes || null,
  };
};

const isTextInputTarget = (target: EventTarget | null): boolean => {
  const element = target as HTMLElement | null;
  if (!element) return false;
  const tag = element.tagName.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || element.isContentEditable;
};

export const UniversalVideoWorkbench = ({
  workspacePath,
  title,
  mode = 'marking',
  initialInSec = null,
  initialOutSec = null,
  onUseAsSource,
  onSendToCut,
  onSendToRender,
}: UniversalVideoWorkbenchProps) => {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [metadata, setMetadata] = useState<MediaMetadata | null>(null);
  const [segments, setSegments] = useState<VideoSegment[]>([]);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [inSec, setInSec] = useState<number | null>(initialInSec);
  const [outSec, setOutSec] = useState<number | null>(initialOutSec);
  const [fit, setFit] = useState<FitMode>('contain');
  const [speed, setSpeed] = useState(1);
  const [loopSelection, setLoopSelection] = useState(false);
  const [showSafeZones, setShowSafeZones] = useState(false);
  const [showCropFrame, setShowCropFrame] = useState(false);
  const [showMetadata, setShowMetadata] = useState(true);
  const [selectionPlayback, setSelectionPlayback] = useState(false);
  const [label, setLabel] = useState('');
  const [notes, setNotes] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  const videoUrl = useMemo(() => mediaApi.videoUrl(workspacePath), [workspacePath]);
  const selectedDuration = inSec !== null && outSec !== null && outSec > inSec
    ? outSec - inSec
    : null;
  const selection = selectedPayload(workspacePath, inSec, outSec, label, notes);
  const timelineDuration = duration || metadata?.duration_sec || 0;
  const inPercent = timelineDuration && inSec !== null ? (inSec / timelineDuration) * 100 : 0;
  const outPercent = timelineDuration && outSec !== null ? (outSec / timelineDuration) * 100 : 0;
  const rangeLeft = Math.min(inPercent, outPercent);
  const rangeWidth = outSec !== null && inSec !== null && outSec > inSec
    ? Math.max(0, outPercent - inPercent)
    : 0;

  const refreshSegments = async () => {
    if (!workspacePath) return;
    const data = await mediaApi.segments(workspacePath);
    setSegments(data.items);
  };

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setError('');
      setMessage('');
      if (!workspacePath) return;
      try {
        const [meta, segmentData] = await Promise.all([
          mediaApi.metadata(workspacePath),
          mediaApi.segments(workspacePath),
        ]);
        if (cancelled) return;
        setMetadata(meta);
        setSegments(segmentData.items);
        setDuration(meta.duration_sec || 0);
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : String(caught));
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [workspacePath]);

  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = speed;
  }, [speed]);

  useEffect(() => {
    const stopVideo = () => {
      const video = videoRef.current;
      if (!video) return;
      video.pause();
      setSelectionPlayback(false);
    };
    const handleMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      if (event.data?.type === 'shortsfarm:pause-video') stopVideo();
    };
    window.addEventListener('message', handleMessage);
    return () => {
      window.removeEventListener('message', handleMessage);
      stopVideo();
      const video = videoRef.current;
      if (video) {
        video.removeAttribute('src');
        video.load();
      }
    };
  }, []);

  const seekTo = (time: number) => {
    const video = videoRef.current;
    if (!video) return;
    const next = Math.max(0, Math.min(time, timelineDuration || video.duration || 0));
    video.currentTime = next;
    setCurrentTime(next);
  };

  const togglePlay = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) void video.play();
    else video.pause();
  };

  const setIn = () => {
    setInSec(currentTime);
    setError('');
  };

  const setOut = () => {
    setOutSec(currentTime);
    setError('');
  };

  const clearSelection = () => {
    setInSec(null);
    setOutSec(null);
    setSelectionPlayback(false);
    setError('');
  };

  const playSelection = () => {
    if (!selection) {
      setError('Out marker должен быть больше In marker.');
      return;
    }
    setSelectionPlayback(true);
    seekTo(selection.start_sec);
    void videoRef.current?.play();
  };

  const saveSegment = async () => {
    if (!selection) {
      setError('Out marker должен быть больше In marker.');
      return;
    }
    try {
      const result = await mediaApi.createSegment(selection);
      setMessage(`Segment сохранён: #${result.item.id}`);
      setError('');
      setLabel('');
      setNotes('');
      await refreshSegments();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const handleTimeUpdate = () => {
    const video = videoRef.current;
    if (!video) return;
    const time = video.currentTime;
    setCurrentTime(time);
    if (selectionPlayback && selection && time >= selection.end_sec) {
      if (loopSelection) {
        video.currentTime = selection.start_sec;
        void video.play();
      } else {
        video.pause();
        setSelectionPlayback(false);
      }
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (isTextInputTarget(event.target)) return;
    const key = event.key.toLowerCase();
    if (event.code === 'Space' || key === 'k') {
      event.preventDefault();
      togglePlay();
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault();
      seekTo(currentTime - (event.shiftKey ? 1 : 5));
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      seekTo(currentTime + (event.shiftKey ? 1 : 5));
    } else if (key === 'j') {
      event.preventDefault();
      seekTo(currentTime - 5);
    } else if (key === 'l') {
      event.preventDefault();
      seekTo(currentTime + 5);
    } else if (key === 'i') {
      event.preventDefault();
      setIn();
    } else if (key === 'o') {
      event.preventDefault();
      setOut();
    } else if (key === 'c') {
      event.preventDefault();
      clearSelection();
    } else if (key === 'm') {
      event.preventDefault();
      const video = videoRef.current;
      if (video) video.muted = !video.muted;
    } else if (key === 'f') {
      event.preventDefault();
      void rootRef.current?.requestFullscreen?.();
    }
  };

  return (
    <section
      className={`uvw uvw-${mode}`}
      tabIndex={0}
      ref={rootRef}
      onKeyDown={handleKeyDown}
    >
      <div className="uvw-top">
        <div>
          <h2>{title || metadata?.filename || 'Video Workbench'}</h2>
          <p>{workspacePathLabel(workspacePath)}</p>
        </div>
        <div className="ts-row-actions">
          <button onClick={() => void navigator.clipboard?.writeText(workspacePath)}>Copy path</button>
          <button disabled title="Будет подключено к безопасному backend action позже">Open folder</button>
          <button disabled={!onUseAsSource} onClick={() => onUseAsSource?.(workspacePath)}>Use as source</button>
          <button disabled={!selection || !onSendToCut} onClick={() => selection && onSendToCut?.(selection)}>Send to cut</button>
          <button disabled={!selection || !onSendToRender} onClick={() => selection && onSendToRender?.(selection)}>Send to render</button>
        </div>
      </div>

      {error ? <div className="ts-alert error">{error}</div> : null}
      {message ? <div className="ts-alert success">{message}</div> : null}

      <div className="uvw-player-wrap">
        <media-controller>
          <video
            ref={videoRef}
            slot="media"
            src={videoUrl}
            preload="metadata"
            playsInline
            className={`uvw-video fit-${fit}`}
            onLoadedMetadata={(event) => {
              setDuration(event.currentTarget.duration || metadata?.duration_sec || 0);
              event.currentTarget.playbackRate = speed;
            }}
            onTimeUpdate={handleTimeUpdate}
          />
          {showSafeZones ? <div className="uvw-safe-zone" /> : null}
          {showCropFrame ? <div className="uvw-crop-frame" /> : null}
          <media-control-bar>
            <media-play-button />
            <media-seek-backward-button />
            <media-seek-forward-button />
            <media-time-display />
            <media-time-range />
            <media-playback-rate-button />
            <media-mute-button />
            <media-volume-range />
            <media-pip-button />
            <media-fullscreen-button />
          </media-control-bar>
        </media-controller>
      </div>

      <div className="uvw-tools">
        <label>
          <span>Fit</span>
          <select value={fit} onChange={(event) => setFit(event.target.value as FitMode)}>
            <option value="contain">contain</option>
            <option value="cover">cover</option>
            <option value="original">original</option>
            <option value="vertical">vertical frame</option>
          </select>
        </label>
        <label>
          <span>Speed</span>
          <select value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>
            {[0.25, 0.5, 1, 1.25, 1.5, 2].map((value) => (
              <option value={value} key={value}>{value}x</option>
            ))}
          </select>
        </label>
        <label className="apply-check"><input type="checkbox" checked={loopSelection} onChange={(event) => setLoopSelection(event.target.checked)} /><span>Loop selected range</span></label>
        <label className="apply-check"><input type="checkbox" checked={showSafeZones} onChange={(event) => setShowSafeZones(event.target.checked)} /><span>Show safe zones</span></label>
        <label className="apply-check"><input type="checkbox" checked={showCropFrame} onChange={(event) => setShowCropFrame(event.target.checked)} /><span>Show crop frame</span></label>
        <label className="apply-check"><input type="checkbox" checked={showMetadata} onChange={(event) => setShowMetadata(event.target.checked)} /><span>Show metadata panel</span></label>
      </div>

      {showMetadata && metadata ? (
        <dl className="uvw-meta">
          <div><dt>filename</dt><dd>{metadata.filename}</dd></div>
          <div><dt>duration</dt><dd>{fmtTime(metadata.duration_sec)}</dd></div>
          <div><dt>resolution</dt><dd>{metadata.width && metadata.height ? `${metadata.width}×${metadata.height}` : '—'}</dd></div>
          <div><dt>fps</dt><dd>{metadata.fps ?? '—'}</dd></div>
          <div><dt>file size</dt><dd>{fmtBytes(metadata.size_bytes)}</dd></div>
          <div><dt>codec</dt><dd>{metadata.video_codec || '—'} / {metadata.audio_codec || 'no audio'}</dd></div>
          <div><dt>audio</dt><dd>{metadata.has_audio ? 'yes' : 'no'}</dd></div>
          <div><dt>container</dt><dd>{metadata.container || '—'}</dd></div>
        </dl>
      ) : null}

      <div className="uvw-timeline">
        <div className="uvw-time-row">
          <span>{fmtTime(currentTime)}</span>
          <span>{fmtTime(timelineDuration)}</span>
        </div>
        <div className="uvw-seek-wrap">
          <div className="uvw-selected-range" style={{left: `${rangeLeft}%`, width: `${rangeWidth}%`}} />
          {inSec !== null ? <div className="uvw-marker in" style={{left: `${inPercent}%`}}>In</div> : null}
          {outSec !== null ? <div className="uvw-marker out" style={{left: `${outPercent}%`}}>Out</div> : null}
          <input
            aria-label="Workbench timeline"
            type="range"
            min={0}
            max={timelineDuration || 0}
            step={0.01}
            value={currentTime}
            onChange={(event) => seekTo(Number(event.target.value))}
          />
        </div>
        <div className="uvw-selection-info">
          <span>In: {inSec === null ? '—' : fmtTime(inSec)}</span>
          <span>Out: {outSec === null ? '—' : fmtTime(outSec)}</span>
          <span>Selected duration: {selectedDuration === null ? '—' : fmtTime(selectedDuration)}</span>
        </div>
        {mode !== 'viewer' ? (
          <>
            <div className="ts-row-actions">
              <button onClick={setIn}>Set In</button>
              <button onClick={setOut}>Set Out</button>
              <button onClick={clearSelection}>Clear</button>
              <button disabled={!selection} onClick={playSelection}>Play selection</button>
              <button className="primary" disabled={!selection} onClick={() => void saveSegment()}>Save segment</button>
            </div>
            <div className="uvw-segment-form">
              <label><span>Label</span><input value={label} onChange={(event) => setLabel(event.target.value)} /></label>
              <label><span>Notes</span><textarea value={notes} onChange={(event) => setNotes(event.target.value)} /></label>
            </div>
          </>
        ) : null}
      </div>

      {mode !== 'viewer' ? (
        <div className="uvw-segments">
          <h3>Saved segments</h3>
          {segments.map((segment) => (
            <button
              type="button"
              key={segment.id}
              onClick={() => {
                setInSec(segment.start_sec);
                setOutSec(segment.end_sec);
                seekTo(segment.start_sec);
              }}
            >
              <strong>{segment.label || `segment #${segment.id}`}</strong>
              <span>{fmtTime(segment.start_sec)} — {fmtTime(segment.end_sec)} · {fmtTime(segment.duration_sec)}</span>
            </button>
          ))}
          {!segments.length ? <div className="empty-note">Для этого видео segments пока не сохранены.</div> : null}
        </div>
      ) : null}
    </section>
  );
};
