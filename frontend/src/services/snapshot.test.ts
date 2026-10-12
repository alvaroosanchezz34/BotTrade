import type { Overview } from "../api/types";
import { clearStoredData, loadOverview, saveOverview } from "./snapshot";

const fake = { mode: "PAPER" } as Overview;

describe("snapshot local del panel", () => {
  beforeEach(() => localStorage.clear());

  it("guarda y recupera el último resumen", () => {
    saveOverview(fake);
    expect(loadOverview()?.data.mode).toBe("PAPER");
  });

  it("caduca a las 24 h y se borra", () => {
    saveOverview(fake);
    const later = Date.now() + 25 * 3600 * 1000;
    expect(loadOverview(later)).toBeNull();
    expect(localStorage.length).toBe(0);
  });

  it("se borra al cerrar sesión", () => {
    saveOverview(fake);
    clearStoredData();
    expect(loadOverview()).toBeNull();
  });

  it("ignora datos corruptos", () => {
    localStorage.setItem("bottrade.lastOverview.v1", "{no json");
    expect(loadOverview()).toBeNull();
  });
});
