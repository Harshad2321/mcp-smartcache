import React from 'react';

interface MetricCardProps {
  title: string;
  value: string | number;
  sublabel?: string;
  icon?: React.ReactNode;
  badge?: {
    text: string;
    variant: 'positive' | 'neutral' | 'warning' | 'purple';
  };
  className?: string;
}

export const MetricCard: React.FC<MetricCardProps> = ({
  title,
  value,
  sublabel,
  icon,
  badge,
  className = '',
}) => {
  return (
    <div className={`metric-card ${className}`}>
      <div className="metric-header">
        <span className="metric-title">{title}</span>
        {icon && <div className="metric-icon">{icon}</div>}
      </div>
      <div className="metric-value-row">
        <span className="metric-value">{value}</span>
        {badge && (
          <span className={`metric-badge badge-${badge.variant}`}>
            {badge.text}
          </span>
        )}
      </div>
      {sublabel && <div className="metric-sublabel">{sublabel}</div>}
    </div>
  );
};

