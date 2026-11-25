// Keep the agent on an isolated Docker network. Only connect to the relay socket.
import net from "node:net";
net.createServer(client => {
  const upstream = net.createConnection("/inference/gmi.sock");
  client.on("error", () => upstream.destroy());
  upstream.on("error", () => client.destroy());
  client.on("close", () => upstream.destroy());
  client.pipe(upstream).pipe(client);
}).listen(18981, "127.0.0.1", () => console.log("relay ready"));
