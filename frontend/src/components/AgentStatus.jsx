import React from 'react';
import { motion } from 'framer-motion';
import { 
  Search, FileText, CheckCircle, List, PenTool, Quote, 
  Circle, Loader2, CheckCircle2, AlertCircle 
} from 'lucide-react';
import { cn } from '../lib/utils';

const AGENT_ICONS = {
  research: Search,
  document: FileText,
  factcheck: CheckCircle,
  outline: List,
  synthesis: PenTool,
  citations: Quote,
};

const AGENTS = [
  { key: 'research', label: 'Web Research', description: 'Gathering sources' },
  { key: 'document', label: 'Documents', description: 'Processing files' },
  { key: 'factcheck', label: 'Fact Check', description: 'Verifying claims' },
  { key: 'outline', label: 'Outline', description: 'Structuring report' },
  { key: 'synthesis', label: 'Synthesis', description: 'Writing sections' },
  { key: 'citations', label: 'Citations', description: 'Formatting refs' },
];

function getAgentStatus(agentKey, currentAgent, completedAgents, hasDocuments) {
  if (agentKey === 'document' && !hasDocuments) {
    return 'skipped';
  }
  if (completedAgents.includes(agentKey)) {
    return 'complete';
  }
  if (currentAgent === agentKey) {
    return 'running';
  }
  return 'pending';
}

function AgentStatusItem({ agent, status }) {
  const Icon = AGENT_ICONS[agent.key];
  
  const statusConfig = {
    pending: {
      icon: Circle,
      color: 'text-zinc-600',
      bg: 'bg-zinc-800/30',
      border: 'border-zinc-800',
    },
    running: {
      icon: Loader2,
      color: 'text-amber-400',
      bg: 'bg-amber-500/10',
      border: 'border-amber-500/30',
    },
    complete: {
      icon: CheckCircle2,
      color: 'text-emerald-400',
      bg: 'bg-emerald-500/10',
      border: 'border-emerald-500/30',
    },
    skipped: {
      icon: Circle,
      color: 'text-zinc-700',
      bg: 'bg-zinc-900/50',
      border: 'border-zinc-800/50',
    },
    error: {
      icon: AlertCircle,
      color: 'text-rose-400',
      bg: 'bg-rose-500/10',
      border: 'border-rose-500/30',
    },
  };
  
  const config = statusConfig[status] || statusConfig.pending;
  const StatusIcon = config.icon;
  
  return (
    <motion.div
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      className={cn(
        'flex items-center gap-3 px-3 py-2.5 rounded-sm border transition-all duration-300',
        config.bg,
        config.border,
        status === 'running' && 'agent-running',
        status === 'skipped' && 'opacity-40'
      )}
    >
      <div className={cn('p-1.5 rounded-sm', config.bg)}>
        <Icon className={cn('w-4 h-4', config.color)} strokeWidth={1.5} />
      </div>
      <div className="flex-1 min-w-0">
        <p className={cn('text-sm font-medium truncate', config.color)}>
          {agent.label}
        </p>
        <p className="text-xs text-zinc-600 truncate">
          {status === 'running' ? 'Processing...' : agent.description}
        </p>
      </div>
      <StatusIcon 
        className={cn(
          'w-4 h-4 flex-shrink-0',
          config.color,
          status === 'running' && 'animate-spin'
        )} 
        strokeWidth={1.5}
      />
    </motion.div>
  );
}

export function AgentStatusPanel({ currentAgent, completedAgents, hasDocuments }) {
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-mono uppercase tracking-wider text-zinc-500 px-1 mb-3">
        Agent Pipeline
      </h3>
      {AGENTS.map((agent, index) => {
        const status = getAgentStatus(agent.key, currentAgent, completedAgents, hasDocuments);
        return (
          <motion.div
            key={agent.key}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: index * 0.05 }}
          >
            <AgentStatusItem agent={agent} status={status} />
          </motion.div>
        );
      })}
    </div>
  );
}

export default AgentStatusPanel;
