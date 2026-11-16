// Emite una factura por cliente de seed/clientes.json, la ancla en Besu y deja el documento cifrado off-chain.
// Escenarios: factura_alta e pago_no_reflejado -> íntegras; factura_alterada -> se altera tras anclar; sin_anclar -> no se ancla.
// Uso (desde retos/r3-blockchain/contracts-hardhat, con e1.json desplegado):
//   LLAVE_DOCUMENTOS_HEX=<64 hex> npx hardhat run --network besu ../../../seed/anclar-facturas.js
const fs = require("fs");
const path = require("path");
const { ethers } = require("hardhat");

const E1 = path.join(__dirname, "..", "retos", "r3-blockchain", "experimentos", "e1-integridad");
const { Almacen } = require(path.join(E1, "src", "almacen"));
const { crearRng, generarFactura } = require(path.join(E1, "src", "generador"));
const { huellaDeFactura, cuentaSeudonima, sha256Hex } = require(path.join(E1, "src", "huella"));
const { ALTERACIONES } = require(path.join(E1, "src", "alteraciones"));

async function main() {
  const llave = Buffer.from(process.env.LLAVE_DOCUMENTOS_HEX ?? "", "hex");
  if (llave.length !== 32) throw new Error("LLAVE_DOCUMENTOS_HEX debe ser de 32 bytes en hexadecimal");
  const clientes = JSON.parse(fs.readFileSync(path.join(__dirname, "clientes.json"), "utf8"));
  const despliegue = JSON.parse(fs.readFileSync(path.join(E1, "..", "..", "deployments", "e1.json"), "utf8"));
  const [firmante] = await ethers.getSigners();
  const registro = await ethers.getContractAt("IRegistroFacturas", despliegue.RegistroFacturas.address, firmante);
  const almacen = new Almacen(path.join(E1, ".datos"), llave);

  const rng = crearRng("voltera-copiloto");
  const sal = sha256Hex("sal-voltera-copiloto");
  const salida = [];
  const aAnclar = [];
  for (const [i, c] of clientes.entries()) {
    const g = generarFactura(rng, i + 1, sal);
    g.factura.cliente = { nic: c.nic, nombre: c.nombre, direccion: c.direccion };
    g.factura.comunidad = c.comunidad;
    if (c.escenario === "factura_alta") {
      // Compras P2P elevadas: explican una factura alta.
      g.factura.lineas = [
        { concepto: "red", cop: 190000, wh: 240000 },
        { concepto: "compra_p2p", cop: 52000, wh: 104000 },
      ];
      g.factura.totales = { cop: 242000, wh: 344000 };
    }
    g.texto = JSON.stringify(g.factura);
    g.item = {
      facturaId: g.item.facturaId,
      huella: huellaDeFactura(g.texto),
      cuenta: cuentaSeudonima(c.nic, sal),
      periodo: 202610,
      wh: BigInt(g.factura.totales.wh),
      cop: BigInt(g.factura.totales.cop),
    };
    almacen.guardar(g.idNegocio, "json", Buffer.from(g.texto, "utf8"));
    almacen.guardar(g.idNegocio, "pdf", g.pdf);
    if (c.escenario !== "sin_anclar") aAnclar.push(g);
    salida.push({ idNegocio: g.idNegocio, nic: c.nic, periodo: "2026-10", wh: g.factura.totales.wh, cop: g.factura.totales.cop,
      escenario: c.escenario, anclada: c.escenario !== "sin_anclar", lineas: g.factura.lineas, _g: g });
  }

  const tx = await registro.registrarLote(aAnclar.map((g) => g.item), { gasLimit: 100000 + 35000 * aAnclar.length });
  const recibo = await tx.wait();
  console.log(`Ancladas ${aAnclar.length} facturas en el bloque ${recibo.blockNumber}, tx ${tx.hash}`);

  for (const s of salida) {
    if (s.escenario === "factura_alterada") {
      ALTERACIONES.monto_1_cop({ idNegocio: s.idNegocio, texto: s._g.texto, almacen });
      console.log(`Alterada tras el anclaje: ${s.idNegocio}`);
    }
    s.txHash = s.anclada ? tx.hash : null;
    delete s._g;
  }
  fs.writeFileSync(path.join(__dirname, "out", "facturas.json"), JSON.stringify(salida, null, 2));
  console.log(salida.map((s) => `${s.idNegocio} ${s.nic} ${s.escenario}`).join("\n"));
}
main().catch((e) => { console.error(e); process.exit(1); });
