import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BotControls, CONFIRM_TIMEOUT_MS } from "./control";
import { accepted, command } from "../test/fixtures";
import { mockFetch, renderWithProviders } from "../test/utils";

const ui = (over: Partial<Parameters<typeof BotControls>[0]> = {}) => (
  <BotControls entriesAllowed paused={false} killSwitch={false} isAdmin {...over} />
);

describe("controles remotos", () => {
  beforeEach(() => {
    document.cookie = "bt_csrf=tok; path=/";
  });

  it("el kill switch exige marcar la confirmación y no envía nada antes", async () => {
    const m = mockFetch();
    const user = userEvent.setup();
    renderWithProviders(ui());
    await user.click(screen.getByRole("button", { name: "Kill switch" }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Activar kill switch" });
    expect(confirm).toBeDisabled();
    await user.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    expect(m.calls).toHaveLength(0);
  });

  it("solicitado ≠ confirmado: solo se confirma cuando el engine responde APPLIED", async () => {
    let status: "PENDING" | "APPLIED" = "PENDING";
    const m = mockFetch({
      "/api/bot/kill-switch": () => ({ status: 202, body: accepted() }),
      "/api/commands/c1": () => ({ body: command({ status, result: status === "APPLIED" ? "Kill switch activo: 0 órdenes canceladas" : null }) }),
    });
    const user = userEvent.setup();
    renderWithProviders(ui());
    await user.click(screen.getByRole("button", { name: "Kill switch" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("checkbox", { name: /Entiendo que se bloquean/ }));
    await user.click(within(dialog).getByRole("button", { name: "Activar kill switch" }));

    expect(await screen.findByText(/Solicitada, pendiente de confirmación/)).toBeInTheDocument();
    expect(screen.queryByText(/Confirmada por el engine/)).not.toBeInTheDocument();
    const post = m.calls.find((c) => c.method === "POST")!;
    expect(post.body).toEqual({ confirm: "KILL", liquidate_positions: false });
    expect(post.headers["X-CSRF-Token"]).toBe("tok");
    // Mientras está pendiente, los botones de control están bloqueados (sin dobles toques).
    expect(screen.getByRole("button", { name: "Kill switch" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pausar entradas" })).toBeDisabled();

    status = "APPLIED";
    expect(await screen.findByText(/Confirmada por el engine/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByText(/Kill switch activo/)).toBeInTheDocument();
  });

  it("liquidar posiciones es opcional y va desmarcado por defecto", async () => {
    const m = mockFetch({
      "/api/bot/kill-switch": () => ({ status: 202, body: accepted() }),
      "/api/commands/c1": () => ({ body: command() }),
    });
    const user = userEvent.setup();
    renderWithProviders(ui());
    await user.click(screen.getByRole("button", { name: "Kill switch" }));
    const dialog = await screen.findByRole("dialog");
    const liquidate = within(dialog).getByRole("checkbox", { name: /liquidar las posiciones/ });
    expect(liquidate).not.toBeChecked();
    await user.click(liquidate);
    await user.click(within(dialog).getByRole("checkbox", { name: /Entiendo que se bloquean/ }));
    await user.click(within(dialog).getByRole("button", { name: "Activar kill switch" }));
    await waitFor(() => expect(m.calls.some((c) => c.method === "POST")).toBe(true));
    expect(m.calls.find((c) => c.method === "POST")!.body).toEqual({ confirm: "KILL", liquidate_positions: true });
  });

  it("una reanudación rechazada muestra el motivo y no se presenta como hecha", async () => {
    mockFetch({
      "/api/bot/resume": () => ({ status: 202, body: accepted() }),
      "/api/commands/c1": () => ({ body: command({ command: "RESUME", status: "REJECTED", result: "Reanudación rechazada: conexiones no operativas" }) }),
    });
    const user = userEvent.setup();
    renderWithProviders(ui({ paused: true }));
    await user.click(screen.getByRole("button", { name: "Reanudar" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Solicitar reanudación" }));
    expect(await screen.findByText(/Rechazada\./)).toBeInTheDocument();
    expect(screen.getByText(/conexiones no operativas/)).toBeInTheDocument();
    expect(screen.queryByText(/Confirmada/)).not.toBeInTheDocument();
  });

  it("reanudar tras un kill switch exige confirmar que se revisó la causa", async () => {
    mockFetch();
    const user = userEvent.setup();
    renderWithProviders(ui({ killSwitch: true }));
    await user.click(screen.getByRole("button", { name: "Reanudar" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Solicitar reanudación" })).toBeDisabled();
    await user.click(within(dialog).getByRole("checkbox"));
    expect(within(dialog).getByRole("button", { name: "Solicitar reanudación" })).toBeEnabled();
  });

  it("si el engine nunca confirma, queda «Sin confirmar» (no «hecho»)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mockFetch({
        "/api/bot/pause": () => ({ status: 202, body: accepted({ engine_alive: false, detail: "Solicitud registrada, pero el engine no responde: no está confirmada." }) }),
        "/api/commands/c1": () => ({ body: command({ command: "PAUSE" }) }),
      });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      renderWithProviders(ui());
      await user.click(screen.getByRole("button", { name: "Pausar entradas" }));
      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: "Solicitar pausa" }));
      expect(await screen.findByText(/pendiente de confirmación/)).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(CONFIRM_TIMEOUT_MS + 1000);
      });
      expect(await screen.findByText(/Sin confirmar/)).toBeInTheDocument();
      expect(screen.getByText(/NO está confirmada/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("un error del servidor (429) se muestra y no deja la acción a medias", async () => {
    mockFetch({ "/api/bot/pause": () => ({ status: 429, body: { detail: "Demasiadas peticiones" } }) });
    const user = userEvent.setup();
    renderWithProviders(ui());
    await user.click(screen.getByRole("button", { name: "Pausar entradas" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Solicitar pausa" }));
    expect(await screen.findByText(/No se pudo enviar/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pausar entradas" })).toBeEnabled();
  });

  it("un usuario de solo lectura no ve botones de control", () => {
    renderWithProviders(ui({ isAdmin: false }));
    expect(screen.queryByRole("button", { name: "Kill switch" })).not.toBeInTheDocument();
    expect(screen.getByText(/solo lectura/)).toBeInTheDocument();
  });

  it("no existe ningún control para activar dinero real", () => {
    renderWithProviders(ui());
    expect(screen.queryByText(/dinero real|live|producción/i)).not.toBeInTheDocument();
  });
});
