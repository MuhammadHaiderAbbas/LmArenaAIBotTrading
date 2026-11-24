// Accessible, dependency-light design system primitives.
import {
  createContext,
  useCallback,
  useContext,
  useId,
  useMemo,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
} from "react";
import clsx from "clsx";

// ---------------------------------------------------------------------------
// Buttons
// ---------------------------------------------------------------------------
type Variant = "primary" | "secondary" | "ghost" | "danger" | "success";
type Size = "sm" | "md" | "lg";

const variants: Record<Variant, string> = {
  primary:
    "bg-accent text-white hover:bg-accent-soft focus-visible:ring-accent border border-accent/60 shadow-[0_6px_18px_-10px_rgba(79,140,255,0.9)]",
  secondary:
    "bg-surface-raised text-ink border border-line-strong hover:bg-surface-hover hover:border-ink-faint",
  ghost: "bg-transparent text-ink-muted border border-transparent hover:text-ink hover:bg-surface-raised",
  danger: "bg-negative/15 text-negative border border-negative/40 hover:bg-negative/25",
  success: "bg-positive/15 text-positive border border-positive/40 hover:bg-positive/25",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-xs rounded-md gap-1.5",
  md: "h-10 px-4 text-sm rounded-lg gap-2",
  lg: "h-11 px-5 text-sm rounded-lg gap-2",
};

export function Button({
  variant = "primary",
  size = "md",
  className,
  loading,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}) {
  return (
    <button
      {...props}
      disabled={props.disabled || loading}
      className={clsx(
        "inline-flex select-none items-center justify-center font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        variants[variant],
        sizes[size],
        className,
      )}
    >
      {loading && (
        <span
          aria-hidden
          className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
        />
      )}
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Inputs
// ---------------------------------------------------------------------------
export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="label block text-ink-muted">
        {label}
      </label>
      {children}
      {error ? (
        <p className="text-xs text-negative" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs text-ink-faint">{hint}</p>
      ) : null}
    </div>
  );
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <input
      id={props.id ?? id}
      {...props}
      className={clsx(
        "h-10 w-full rounded-lg border border-line-strong bg-surface-sunken px-3 text-sm text-ink placeholder:text-ink-faint",
        "transition-colors hover:border-ink-faint focus:border-accent",
        className,
      )}
    />
  );
}

export function Select({
  className,
  children,
  ...props
}: InputHTMLAttributes<HTMLSelectElement> & { children: ReactNode }) {
  return (
    <select
      {...props}
      className={clsx(
        "h-10 w-full rounded-lg border border-line-strong bg-surface-sunken px-3 text-sm text-ink",
        "transition-colors hover:border-ink-faint focus:border-accent",
        className,
      )}
    >
      {children}
    </select>
  );
}

// ---------------------------------------------------------------------------
// Layout primitives
// ---------------------------------------------------------------------------
export function Panel({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClassName,
  scrollBody = false,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  /**
   * Let the body scroll sideways. Panels clip their content (`overflow-hidden`) so a
   * table cannot spill out of the rounded corners — which means any table inside a
   * Panel *must* get its own horizontal scroll container or it is simply cut off on a
   * narrow screen. This flag exists so that is one decision per panel, not a habit
   * of remembering.
   */
  scrollBody?: boolean;
}) {
  return (
    <section className={clsx("panel overflow-hidden", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold tracking-tight text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div
        className={clsx(
          scrollBody && "overflow-x-auto",
          bodyClassName ?? "p-5",
        )}
      >
        {children}
      </div>
    </section>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        {eyebrow && <p className="label mb-1.5">{eyebrow}</p>}
        <h1 className="text-2xl font-semibold tracking-tight text-white">{title}</h1>
        {description && <p className="mt-1.5 max-w-2xl text-sm text-ink-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone = "neutral",
  icon,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "neutral" | "positive" | "negative" | "accent";
  icon?: ReactNode;
}) {
  const toneClass =
    tone === "positive"
      ? "text-positive"
      : tone === "negative"
        ? "text-negative"
        : tone === "accent"
          ? "text-accent-soft"
          : "text-white";
  return (
    <div className="panel p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="label">{label}</p>
        {icon && <span className="text-ink-faint">{icon}</span>}
      </div>
      <p className={clsx("mt-2 font-mono text-xl font-semibold tabular-nums tracking-tight", toneClass)}>
        {value}
      </p>
      {hint && <p className="mt-1.5 text-xs text-ink-faint">{hint}</p>}
    </div>
  );
}

export function Badge({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: "neutral" | "positive" | "negative" | "accent" | "caution" | "muted";
  className?: string;
}) {
  const tones = {
    neutral: "border-line-strong bg-surface-raised text-ink-muted",
    positive: "border-positive/30 bg-positive/10 text-positive",
    negative: "border-negative/30 bg-negative/10 text-negative",
    accent: "border-accent/40 bg-accent/10 text-accent-soft",
    caution: "border-caution/30 bg-caution/10 text-caution",
    muted: "border-line bg-surface-sunken text-ink-faint",
  } as const;
  return <span className={clsx("badge", tones[tone], className)}>{children}</span>;
}

export function Dot({ tone = "positive", pulse }: { tone?: "positive" | "negative" | "caution" | "muted"; pulse?: boolean }) {
  const colors = {
    positive: "bg-positive",
    negative: "bg-negative",
    caution: "bg-caution",
    muted: "bg-ink-faint",
  } as const;
  return (
    <span
      aria-hidden
      className={clsx("inline-block h-1.5 w-1.5 rounded-full", colors[tone], pulse && "animate-pulse-dot")}
    />
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-line-strong bg-surface-sunken/60 px-6 py-12 text-center">
      {icon && <div className="mb-3 text-ink-faint">{icon}</div>}
      <p className="text-sm font-semibold text-ink">{title}</p>
      <p className="mt-1 max-w-md text-xs leading-relaxed text-ink-muted">{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-16 text-sm text-ink-muted" role="status">
      <span
        aria-hidden
        className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent"
      />
      {label}…
    </div>
  );
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-negative/40 bg-negative/10 px-4 py-3 text-sm text-negative"
    >
      <span>{message}</span>
      {onRetry && (
        <Button size="sm" variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

export function KeyValue({ items }: { items: Array<{ label: string; value: ReactNode }> }) {
  return (
    <dl className="divide-y divide-line/70">
      {items.map((item) => (
        <div key={item.label} className="flex items-start justify-between gap-4 py-2.5">
          <dt className="text-xs text-ink-muted">{item.label}</dt>
          <dd className="text-right text-xs font-medium text-ink">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

// ---------------------------------------------------------------------------
// Toasts
// ---------------------------------------------------------------------------
type Toast = { id: number; title: string; description?: string; tone: "success" | "error" | "info" };
const ToastContext = createContext<{ push: (toast: Omit<Toast, "id">) => void }>({ push: () => {} });

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((toast: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { ...toast, id }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((t) => t.id !== id));
    }, 5200);
  }, []);

  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        aria-atomic="false"
        className="pointer-events-none fixed bottom-6 right-6 z-50 flex w-full max-w-sm flex-col gap-2"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={clsx(
              "pointer-events-auto animate-slide-up rounded-lg border px-4 py-3 shadow-panel",
              toast.tone === "success"
                ? "border-positive/40 bg-surface-raised"
                : toast.tone === "error"
                  ? "border-negative/40 bg-surface-raised"
                  : "border-line-strong bg-surface-raised",
            )}
          >
            <p
              className={clsx(
                "text-sm font-semibold",
                toast.tone === "success"
                  ? "text-positive"
                  : toast.tone === "error"
                    ? "text-negative"
                    : "text-ink",
              )}
            >
              {toast.title}
            </p>
            {toast.description && <p className="mt-0.5 text-xs text-ink-muted">{toast.description}</p>}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext).push;
}
