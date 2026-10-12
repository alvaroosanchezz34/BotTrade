import * as Dialog from "@radix-ui/react-dialog";
import { useState, type ReactNode } from "react";

export interface Confirmation {
  id: string;
  label: string;
  /** Casilla que debe marcarse para poder confirmar. */
  required?: boolean;
  /** Casilla opcional (p. ej. «además liquidar»), desmarcada por defecto. */
  danger?: boolean;
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  tone = "primary",
  checks = [],
  onConfirm,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  tone?: "primary" | "danger";
  checks?: Confirmation[];
  onConfirm: (checked: Record<string, boolean>) => void;
  children?: ReactNode;
}) {
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const ready = checks.filter((c) => c.required).every((c) => checked[c.id]);
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(o) => {
        if (!o) setChecked({});
        onOpenChange(o);
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="dialog" aria-describedby="confirm-desc">
          <Dialog.Title asChild>
            <h2>{title}</h2>
          </Dialog.Title>
          <Dialog.Description asChild>
            <div id="confirm-desc" className="muted" style={{ marginBottom: 12 }}>
              {description}
            </div>
          </Dialog.Description>
          {children}
          <div className="stack" style={{ margin: "12px 0" }}>
            {checks.map((c) => (
              <label key={c.id} className="check">
                <input
                  type="checkbox"
                  checked={!!checked[c.id]}
                  onChange={(e) => setChecked({ ...checked, [c.id]: e.target.checked })}
                />
                <span>{c.label}</span>
              </label>
            ))}
          </div>
          <div className="row" style={{ gap: 10 }}>
            <Dialog.Close asChild>
              <button className="btn block">Cancelar</button>
            </Dialog.Close>
            <button
              className={`btn block ${tone === "danger" ? "danger" : "primary"}`}
              disabled={!ready}
              onClick={() => {
                const result = checked;
                setChecked({});
                onConfirm(result);
              }}
            >
              {confirmLabel}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
