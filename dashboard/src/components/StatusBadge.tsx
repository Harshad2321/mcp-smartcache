import React from 'react';
import { motion } from 'framer-motion';

export type BadgeStatus = 'completed' | 'running' | 'failed' | 'interrupted' | 'pending' | 'skipped' | 'quarantined' | 'healthy' | 'degraded';

interface StatusBadgeProps {
  status: BadgeStatus | string;
  size?: 'sm' | 'md';
  pulse?: boolean;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({
  status,
  size = 'md',
  pulse = true,
}) => {
  const norm = status.toLowerCase();

  let dotColor = 'bg-slate-400';
  let textColor = 'text-slate-300';
  let bgColor = 'bg-slate-800/40';
  let borderColor = 'border-slate-700/60';
  let isPulsing = false;
  let label = status;

  switch (norm) {
    case 'completed':
    case 'healthy':
      dotColor = 'bg-emerald-400';
      textColor = 'text-emerald-300';
      bgColor = 'bg-emerald-950/30';
      borderColor = 'border-emerald-500/30';
      label = norm === 'healthy' ? 'Healthy' : 'Completed';
      break;

    case 'running':
      dotColor = 'bg-[#00F0FF]';
      textColor = 'text-[#00F0FF]';
      bgColor = 'bg-[#00F0FF]/10';
      borderColor = 'border-[#00F0FF]/40';
      isPulsing = pulse;
      label = 'Running';
      break;

    case 'failed':
      dotColor = 'bg-rose-500';
      textColor = 'text-rose-300';
      bgColor = 'bg-rose-950/40';
      borderColor = 'border-rose-500/40';
      label = 'Failed';
      break;

    case 'interrupted':
    case 'quarantined':
    case 'degraded':
      dotColor = 'bg-amber-400';
      textColor = 'text-amber-300';
      bgColor = 'bg-amber-950/40';
      borderColor = 'border-amber-500/40';
      label = norm === 'degraded' ? 'Degraded' : norm === 'quarantined' ? 'Quarantined' : 'Interrupted';
      break;

    case 'pending':
    case 'skipped':
    default:
      dotColor = 'bg-slate-400';
      textColor = 'text-slate-400';
      bgColor = 'bg-slate-800/40';
      borderColor = 'border-slate-700/50';
      label = norm === 'skipped' ? 'Skipped' : 'Pending';
      break;
  }

  const px = size === 'sm' ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-1 text-xs';

  return (
    <motion.span
      layout
      transition={{ duration: 0.25 }}
      className={`inline-flex items-center gap-1.5 rounded-full font-mono font-medium border ${bgColor} ${borderColor} ${textColor} ${px}`}
    >
      <span className="relative flex h-2 w-2">
        {isPulsing && (
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#00F0FF] opacity-75" />
        )}
        <span className={`relative inline-flex rounded-full h-2 w-2 ${dotColor}`} />
      </span>
      <span className="capitalize">{label}</span>
    </motion.span>
  );
};
