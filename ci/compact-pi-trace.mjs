// Keep final messages, completed tool results and lifecycle events.
import { once } from "node:events";
import { createInterface } from "node:readline";

const events = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of events) {
  if (/^\{"type":"(?:message_update|tool_execution_update)"/.test(line)) continue;
  if (!process.stdout.write(line + "\n")) await once(process.stdout, "drain");
}
