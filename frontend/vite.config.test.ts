// @vitest-environment node

import { describe, expect, it } from "vitest";
import configFactory from "./vite.config";

describe("development API proxy", () => {
  it("forwards monitor WebSocket upgrades to FastAPI", () => {
    const config = configFactory({
      command: "serve",
      mode: "development",
      isSsrBuild: false,
      isPreview: false,
    });

    expect(config.server?.proxy?.["/api"]).toMatchObject({
      target: "http://127.0.0.1:8000",
      ws: true,
    });
  });
});
