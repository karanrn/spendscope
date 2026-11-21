// Helpers shared by record.mjs and shots.mjs.
import { spawn } from "child_process";
import net from "net";

export const freePort = () => new Promise((resolve, reject) => {
  const srv = net.createServer().on("error", reject);
  srv.listen(0, "127.0.0.1", () => { const { port } = srv.address(); srv.close(() => resolve(port)); });
});

// Serve the demo database with the real dashboard server; resolves once it accepts connections.
export async function startServer(demo, port) {
  const server = spawn(process.env.PYTHON || "python3", ["-m", "spendscope", "serve", "--no-open", "--port", String(port)], { cwd: demo, stdio: "ignore" });
  const started = Date.now();
  for (;;) {
    if (server.exitCode !== null) throw new Error(`dashboard server exited with code ${server.exitCode}`);
    const up = await new Promise(resolve => { const s = net.connect(port, "127.0.0.1", () => { s.end(); resolve(true); }); s.on("error", () => resolve(false)); });
    if (up) return server;
    if (Date.now() - started > 10000) { server.kill(); throw new Error("dashboard server did not start"); }
    await new Promise(r => setTimeout(r, 200));
  }
}
