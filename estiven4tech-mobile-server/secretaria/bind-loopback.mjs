// Carregado com NODE_OPTIONS=--import. O app da secretária chama listen({ host: "0.0.0.0" }),
// o que no UserLAnd/PRoot exporia a porta para toda a rede Wi-Fi (a rede é a do Android).
// Aqui qualquer listen em todas as interfaces vira 127.0.0.1, sem alterar o código do app.
import net from "node:net";

const ANY = new Set([undefined, null, "", "0.0.0.0", "::", "::0"]);
const original = net.Server.prototype.listen;

net.Server.prototype.listen = function (...args) {
  const first = args[0];
  if (first && typeof first === "object" && "port" in first && ANY.has(first.host)) {
    args[0] = { ...first, host: "127.0.0.1" };
  } else if (typeof first === "number" || (typeof first === "string" && /^\d+$/.test(first))) {
    if (typeof args[1] === "string") {
      if (ANY.has(args[1])) args[1] = "127.0.0.1";
    } else {
      args.splice(1, 0, "127.0.0.1");
    }
  }
  return original.apply(this, args);
};
