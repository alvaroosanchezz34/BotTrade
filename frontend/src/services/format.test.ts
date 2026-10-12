import { ago, dateTime, duration, money, num, pct, price, signedMoney, tone } from "./format";

describe("formato", () => {
  it("muestra «—» para datos ausentes, nunca 0", () => {
    expect(money(null)).toBe("—");
    expect(money(undefined)).toBe("—");
    expect(money("")).toBe("—");
    expect(pct(null)).toBe("—");
    expect(price("abc")).toBe("—");
    expect(num("NaN")).toBeNull();
  });

  it("formatea importes Decimal con signo explícito", () => {
    expect(signedMoney("1.5")).toContain("+1,50");
    expect(signedMoney("-0.5")).toContain("-0,50");
    expect(signedMoney("0")).toBe("0,00 USDT");
    expect(money("500.000000000000000000")).toBe("500,00 USDT");
  });

  it("el color nunca sustituye al signo y el cero es neutro", () => {
    expect(tone("1")).toBe("pos");
    expect(tone("-1")).toBe("neg");
    expect(tone("0")).toBe("flat");
    expect(tone(null)).toBe("flat");
  });

  it("convierte UTC a la zona elegida", () => {
    const iso = "2026-03-02T12:00:00Z";
    expect(dateTime(iso, "UTC")).toContain("12:00:00");
    expect(dateTime(iso, "Asia/Tokyo")).toContain("21:00:00");
    expect(dateTime(null)).toBe("—");
    expect(dateTime("no-fecha")).toBe("—");
  });

  it("tiempos relativos y duraciones", () => {
    const now = Date.parse("2026-03-02T12:00:00Z");
    expect(ago("2026-03-02T11:59:30Z", now)).toBe("hace 30 s");
    expect(ago("2026-03-02T10:00:00Z", now)).toBe("hace 2 h");
    expect(duration(90)).toBe("2 min");
    expect(duration(null)).toBe("—");
  });
});
