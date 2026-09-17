import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { Pause, Play, Compass, PauseCircle } from 'lucide-react';
import { Button } from './ui/button';

// Poll-bounded ACK/error feedback shared by the running and paused views.
// The backend only guarantees the command was queued under the per-session
// lock — every message states the honest 2-5s polling cadence instead of
// implying an instant effect.
const ACK_MESSAGES = {
  pause:
    'Pause requested — the run stops at the next agent step boundary, usually within a few seconds (status polls every 2–5s).',
  resume:
    'Resume requested — the run continues within a few seconds (status polls every 2–5s).',
  redirect:
    'Redirect queued — the focus applies at the next section boundary, usually within a few seconds (status polls every 2–5s).',
};

export function SteerFeedback({ steerAck, steerError }) {
  if (steerError) {
    return (
      <div
        data-testid="steer-error"
        className="flex items-center gap-2 px-3 py-2 rounded-sm bg-rose-500/10 border border-rose-500/30"
      >
        <p className="text-xs text-rose-400">{steerError}</p>
      </div>
    );
  }
  if (!steerAck) return null;
  return (
    <div
      data-testid="steer-ack"
      className="flex items-center gap-2 px-3 py-2 rounded-sm bg-cyan-500/10 border border-cyan-500/30"
    >
      <p className="text-xs text-cyan-300">
        {ACK_MESSAGES[steerAck.command] || 'Steering command accepted.'}
      </p>
    </div>
  );
}

// Pause + redirect controls for the running branch (post-approval only —
// the backend rejects steering before outline approval with a 400).
export function SteeringControls({ onSteer, steerInFlight }) {
  const [focus, setFocus] = useState('');

  return (
    <div
      data-testid="steering-controls"
      className="flex flex-col gap-3 w-full max-w-md mt-6"
    >
      <div className="flex items-center gap-3">
        <Button
          data-testid="steer-pause-btn"
          onClick={() => onSteer('pause')}
          disabled={steerInFlight !== null}
          variant="outline"
          className="border-zinc-800 hover:border-amber-500/50 hover:bg-amber-500/10"
        >
          {steerInFlight === 'pause' ? (
            <Pause className="w-4 h-4 mr-2 animate-pulse" />
          ) : (
            <Pause className="w-4 h-4 mr-2" />
          )}
          Pause
        </Button>
        <input
          data-testid="steer-focus-input"
          value={focus}
          onChange={(e) => setFocus(e.target.value)}
          placeholder="Redirect focus — e.g. focus on recent benchmarks"
          className="flex-1 bg-zinc-900 border border-zinc-800 rounded-sm px-3 py-2 text-sm text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:border-cyan-500/50"
          aria-label="Redirect focus"
        />
        <Button
          data-testid="steer-redirect-btn"
          onClick={() => onSteer('redirect', focus)}
          disabled={steerInFlight !== null || !focus.trim()}
          variant="outline"
          className="border-zinc-800 hover:border-cyan-500/50 hover:bg-cyan-500/10"
        >
          {steerInFlight === 'redirect' ? (
            <Compass className="w-4 h-4 mr-2 animate-pulse" />
          ) : (
            <Compass className="w-4 h-4 mr-2" />
          )}
          Redirect
        </Button>
      </div>
    </div>
  );
}

// Paused-status view (W4). OutlineApproval interaction precedent: a calm
// centered card that explains the state and offers resume + redirect.
export function PausedState({
  topic,
  streamUpdates,
  onSteer,
  steerInFlight,
  researchRounds,
}) {
  const [focus, setFocus] = useState('');
  const latestUpdate =
    streamUpdates && streamUpdates.length > 0
      ? streamUpdates[streamUpdates.length - 1]
      : 'Run parked — no agent steps are executing.';

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      data-testid="paused-state"
      className="h-full flex flex-col items-center justify-center text-center p-8"
    >
      <div className="p-4 rounded-sm bg-amber-500/10 border border-amber-500/30 mb-6">
        <PauseCircle className="w-10 h-10 text-amber-400" strokeWidth={1.5} />
      </div>
      <h2 className="text-xl font-display font-semibold text-zinc-200 mb-2">
        Research paused
      </h2>
      <p className="text-zinc-500 max-w-lg text-sm mb-1">
        The run on <span className="text-zinc-300">&ldquo;{topic}&rdquo;</span> is
        parked mid-pipeline. Resume it, or redirect with focus text that steers
        the remaining sections.
      </p>
      {typeof researchRounds === 'number' && researchRounds > 0 && (
        <p data-testid="paused-round-info" className="text-xs text-zinc-600 font-mono mb-2">
          {researchRounds} deep-research round{researchRounds === 1 ? '' : 's'} completed
        </p>
      )}
      <p className="text-zinc-600 max-w-lg text-xs font-mono mb-6">{latestUpdate}</p>

      <div className="flex flex-col items-center gap-3 w-full max-w-md">
        <Button
          data-testid="steer-resume-btn"
          onClick={() => onSteer('resume')}
          disabled={steerInFlight !== null}
          className="bg-cyan-500/10 border border-cyan-500/30 hover:bg-cyan-500/20 text-cyan-300"
        >
          {steerInFlight === 'resume' ? (
            <Play className="w-4 h-4 mr-2 animate-pulse" />
          ) : (
            <Play className="w-4 h-4 mr-2" />
          )}
          Resume research
        </Button>
        <div className="flex items-center gap-3 w-full">
          <input
            data-testid="steer-focus-input"
            value={focus}
            onChange={(e) => setFocus(e.target.value)}
            placeholder="Redirect focus — e.g. focus on recent benchmarks"
            className="flex-1 bg-zinc-900 border border-zinc-800 rounded-sm px-3 py-2 text-sm text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:border-cyan-500/50"
            aria-label="Redirect focus"
          />
          <Button
            data-testid="steer-redirect-btn"
            onClick={() => onSteer('redirect', focus)}
            disabled={steerInFlight !== null || !focus.trim()}
            variant="outline"
            className="border-zinc-800 hover:border-cyan-500/50 hover:bg-cyan-500/10"
          >
            <Compass className="w-4 h-4 mr-2" />
            Redirect
          </Button>
        </div>
      </div>
    </motion.div>
  );
}

export default SteeringControls;
