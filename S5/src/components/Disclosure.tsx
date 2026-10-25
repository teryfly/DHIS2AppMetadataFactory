import { useId, useState, type ReactNode } from 'react';
import { Icon } from './icons';

/** Progressive disclosure for secondary work (manual authoring, advanced settings, reject forms). */
export function Disclosure({
  label,
  description,
  children,
  defaultOpen = false,
  className,
}: {
  label: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <section className={`disclosure${open ? ' disclosure--open' : ''}${className ? ` ${className}` : ''}`}>
      <button
        type="button"
        className="disclosure__trigger"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="disclosure__copy">
          <span className="disclosure__label">{label}</span>
          {description ? <span className="disclosure__description">{description}</span> : null}
        </span>
        <Icon name="chevronDown" size={18} className="disclosure__chevron" />
      </button>
      {open ? (
        <div id={id} className="disclosure__panel">
          {children}
        </div>
      ) : null}
    </section>
  );
}
