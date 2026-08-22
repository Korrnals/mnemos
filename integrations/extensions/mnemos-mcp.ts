/**
 * mnemos-mcp — MCP bridge for the Pi coding agent.
 *
 * Pi (npm @earendil-works/pi-coding-agent)
 * has no built-in MCP client by design: tools arrive via TypeScript
 * extensions. This extension spawns `mnemos mcp-server` over stdio, performs
 * the MCP handshake and registers every `mnemos_*` tool as a native Pi tool.
 *
 * Deployed by:  mnemos integration setup --target pi
 * Location:     ~/.pi/agent/extensions/mnemos-mcp.ts
 * Requires:     `mnemos` on PATH (override with MNEMOS_BIN env var).
 * Reload:       /reload  (Pi hot-reloads extensions) or /mnemos to reconnect.
 */

import { spawn, type ChildProcess } from "node:child_process";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const MNEMOS_BIN = process.env.MNEMOS_BIN ?? "mnemos";
const REQ_TIMEOUT_MS = 60_000;

interface McpTool {
	name: string;
	description?: string;
	inputSchema?: Record<string, unknown>;
}

export default function mnemosMcpBridge(pi: ExtensionAPI) {
	let child: ChildProcess | null = null;
	let nextId = 1;
	let buffer = "";
	let started = false;
	let registeredNames = new Set<string>();
	const pending = new Map<
		number,
		{ resolve: (v: unknown) => void; reject: (e: Error) => void; timer: NodeJS.Timeout }
	>();

	// ── JSON-RPC plumbing ──────────────────────────────────────────────────
	function send(obj: unknown): void {
		if (!child?.stdin?.writable) throw new Error("mnemos MCP: stdin not writable");
		child.stdin.write(JSON.stringify(obj) + "\n");
	}

	function request(method: string, params?: unknown): Promise<any> {
		const id = nextId++;
		return new Promise((resolve, reject) => {
			const timer = setTimeout(() => {
				pending.delete(id);
				reject(new Error(`mnemos MCP: timeout on ${method}`));
			}, REQ_TIMEOUT_MS);
			pending.set(id, { resolve, reject, timer });
			send({ jsonrpc: "2.0", id, method, params });
		});
	}

	function handleLine(line: string): void {
		line = line.trim();
		if (!line) return;
		let msg: any;
		try {
			msg = JSON.parse(line);
		} catch {
			return; // non-RPC noise on stdout
		}
		if (msg.id !== undefined && pending.has(msg.id)) {
			const p = pending.get(msg.id)!;
			pending.delete(msg.id);
			clearTimeout(p.timer);
			if (msg.error) p.reject(new Error(msg.error.message ?? JSON.stringify(msg.error)));
			else p.resolve(msg.result);
		}
		// Server notifications (progress, …) are intentionally ignored.
	}

	function killChild(): void {
		child?.stdin?.end();
		child?.kill("SIGTERM");
		child = null;
		started = false;
	}

	async function startBridge(): Promise<McpTool[]> {
		if (child) killChild();
		buffer = "";
		child = spawn(MNEMOS_BIN, ["mcp-server"], { stdio: ["pipe", "pipe", "ignore"] });
		child.on("error", (e: Error) => {
			started = false;
		});
		child.stdout!.setEncoding("utf8");
		child.stdout!.on("data", (chunk: string) => {
			buffer += chunk;
			let nl: number;
			while ((nl = buffer.indexOf("\n")) >= 0) {
				handleLine(buffer.slice(0, nl));
				buffer = buffer.slice(nl + 1);
			}
		});
		child.on("exit", () => {
			started = false;
			for (const [, p] of pending) {
				clearTimeout(p.timer);
				p.reject(new Error("mnemos MCP: server exited"));
			}
			pending.clear();
		});

		await request("initialize", {
			protocolVersion: "2024-11-05",
			capabilities: {},
			clientInfo: { name: "pi-mnemos-bridge", version: "1.0.0" },
		});
		send({ jsonrpc: "2.0", method: "notifications/initialized" });
		const res = await request("tools/list", {});
		started = true;
		return (res.tools ?? []) as McpTool[];
	}

	// ── Register MCP tools as native Pi tools ───────────────────────────────
	function registerTool(tool: McpTool): boolean {
		if (registeredNames.has(tool.name)) return false;
		const schema =
			tool.inputSchema && tool.inputSchema.type === "object"
				? Type.Unsafe(tool.inputSchema)
				: Type.Object({});

		pi.registerTool({
			name: tool.name,
			label: tool.name.replace(/^mnemos_/, "🧠 "),
			description: tool.description ?? `mnemos MCP tool ${tool.name}`,
			promptSnippet: `Persistent shared memory: ${tool.description?.slice(0, 120) ?? tool.name}`,
			parameters: schema as never,
			async execute(_toolCallId: string, params: unknown) {
				if (!started) await startBridge();
				const result = await request("tools/call", {
					name: tool.name,
					arguments: params,
				});
				const parts: Array<{ type: string; text?: string }> = result.content ?? [];
				const text = parts
					.map((p) => (p.type === "text" ? p.text ?? "" : `[${p.type}]`))
					.join("\n")
					.trim();
				return {
					content: [{ type: "text", text: text || "(empty result)" }],
					details: { isError: result.isError ?? false },
				};
			},
		});
		registeredNames.add(tool.name);
		return true;
	}

	async function connect(ctx: { ui?: { notify: (m: string, l?: string) => void } }): Promise<void> {
		try {
			const tools = await startBridge();
			registeredNames = new Set();
			let fresh = 0;
			for (const t of tools) if (registerTool(t)) fresh++;
			ctx.ui?.notify(`🧠 mnemos: ${fresh} memory tools online (${tools.length} served)`, "info");
		} catch (e) {
			ctx.ui?.notify(`🧠 mnemos: bridge failed — ${(e as Error).message}`, "warning");
		}
	}

	// ── Lifecycle ────────────────────────────────────────────────────────────
	pi.on("session_start", (_event: unknown, ctx: Parameters<Parameters<typeof pi.on>[1]>[1]) =>
		connect(ctx as { ui?: { notify: (m: string, l?: string) => void } }),
	);
	pi.on("session_end", () => killChild());
	process.on("exit", () => killChild());

	// Manual control: /mnemos reconnects the bridge and re-registers tools.
	pi.registerCommand("mnemos", {
		description: "Reconnect the mnemos MCP memory bridge",
		handler: async (_args: string, ctx: any) => connect(ctx),
	});
}
