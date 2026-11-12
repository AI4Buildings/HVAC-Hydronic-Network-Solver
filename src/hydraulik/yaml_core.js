/* yaml_core.js — YAML 1.2 lesen/schreiben für die Schema-Editoren (Hydraulik + Lüftung).

   editor.py setzt diese Datei in beide Templates ein (Platzhalter
   __YAML_CORE_JS__); die Paritätstests laden sie direkt mit node. Semantik
   identisch zu yamlio.py (Python-Loader), der Paritätstest hält beide
   deckungsgleich:
   - YAML 1.2.2 Core Schema: null = ~|null|Null|NULL|(leer);
     bool = true|True|TRUE|false|False|FALSE; int = [-+]?[0-9]+|0o..|0x..;
     float inkl. Exponent (1.4e0, 5e-7), .inf, .nan; alles andere String
     (yes/no/on/off, 1_000, 0b101, 2026-10-05, 12:30 bleiben Strings).
   - Mapping-Schlüssel sind immer Strings im Originaltext ('true:' → "true").
   - Doppelte Schlüssel sind ein Fehler.
   Gelesen werden Block- UND Flow-Stil (Mappings, Listen, einfache und
   gequotete Skalare, Kommentare). Was dieser Parser nicht kann (Anker/Aliase,
   Tags, Block-Skalare | >, mehrzeilige Skalare, explizite/komplexe/leere
   Schlüssel, Tabulatoren außerhalb von Strings, Direktiven, mehrere
   Dokumente), lehnt er mit Meldung und Verweis auf den Server-Parser ab —
   er liest nie still etwas anderes als yamlio.

   Zeichenregeln (gleicher Vertrag wie yamlio):
   - Leerraum ist NUR das Leerzeichen (Tabulatoren außerhalb von Strings lehnt
     dieser Parser ab); Zeilenumbrüche sind LF, CRLF, CR. Geschütztes
     Leerzeichen (U+00A0) und ein U+FEFF mitten im Text sind gewöhnliche
     Zeichen und gehören zum Skalar/Schlüssel; ein BOM entfällt nur ganz am
     Dateianfang.
   - Rohe Steuerzeichen sind überall ein Fehler: C0 außer TAB/LF/CR, DEL, C1
     (inkl. NEL U+0085), U+2028/U+2029 sowie U+FFFE/U+FFFF und einzelne
     Surrogate — in "…" als Escape schreiben (\u0085, \x7f).
   - Ganzzahlen jenseits von ±(2^53 − 1) werden abgelehnt statt ungenau gelesen.
   - Zusätzlich abgelehnt: Zeilen ab Spalte 0, die mit dem Dokumentende '...'
     beginnen; in mehrzeiligen {…}/[…] eine Fortsetzungszeile, die mit '- '
     beginnt, und ein ungequoteter Skalar, der über einen Kommentar (er
     beendet den Skalar, in yamlio ein Syntaxfehler) oder eine Leerzeile
     (yamlio faltet sie zu einem Zeilenumbruch) hinweg weiterläuft.

   Schreiben: Zahlen in Exponentenschreibweise immer mit Punkt und Vorzeichen
   (5.0e-7, auch für YAML-1.1-Werkzeuge eine Zahl); ganzzahlige Werte jenseits
   von 2^53 als Gleitkommazahl (9.007199254740992e+15); Strings, die als Zahl,
   Wahrheitswert oder null gelesen würden (auch YAML-1.1-Wörter yes/no/on/off),
   werden gequotet, Steuerzeichen als Escape geschrieben. */
const YamlCore = (() => {
  const RE_NULL = /^(?:~|null|Null|NULL|)$/;
  const RE_TRUE = /^(?:true|True|TRUE)$/;
  const RE_FALSE = /^(?:false|False|FALSE)$/;
  const RE_INT = /^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$/;
  const RE_FLOAT = /^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$/;
  const YAML11_WORDS = new Set(["yes", "no", "on", "off", "y", "n"]);
  const SERVER_HINT = " – für beliebiges YAML den Editor über 'hydraulik serve' starten.";

  // Rohe Steuerzeichen (mit /u trifft der Surrogatbereich nur EINZELNE Surrogate)
  const RE_CTRL = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u2028\u2029\uFFFE\uFFFF\uD800-\uDFFF]/u;
  // interne Marker (können im Text nicht vorkommen, s. RE_CTRL) hinter einem
  // Zeilenumbruch in einem mehrzeiligen Flow-Wert: CBRK = davor stand ein
  // Kommentar (er beendet den Skalar, wie in yamlio), BBRK = davor lag eine
  // Leerzeile (yamlio faltet sie zu einem Zeilenumbruch — hier nicht unterstützt)
  const CBRK = "\u0001", BBRK = "\u0002";
  const trimSp = s => s.replace(/^ +| +$/g, "");       // nur Leerzeichen, nie U+00A0 & Co.
  const trimEndSp = s => s.replace(/ +$/, "");

  function fail(no, msg) {
    msg = String(msg).replace(/[\u0001\u0002]/g, "");
    return new Error(no ? `YAML (Zeile ${no}): ${msg}` : `YAML: ${msg}`);
  }
  function unsupported(no, what) {
    return fail(no, `${what} wird vom Editor-Parser nicht unterstützt${SERVER_HINT}`);
  }

  /* --- Skalare -------------------------------------------------------------- */
  function resolvePlain(raw, no) {           // ungequoteter Skalar → Core-Schema-Wert
    if (RE_NULL.test(raw)) return null;
    if (RE_TRUE.test(raw)) return true;
    if (RE_FALSE.test(raw)) return false;
    if (RE_INT.test(raw)) {
      const v = raw.startsWith("0o") ? parseInt(raw.slice(2), 8)
              : raw.startsWith("0x") ? parseInt(raw.slice(2), 16) : Number(raw);
      // jenseits von ±(2^53 − 1) rechnet JavaScript nicht mehr exakt — ablehnen
      // statt einen anderen Wert zu lesen als yamlio
      if (!Number.isSafeInteger(v))
        throw fail(no, `Ganzzahl ${raw} liegt außerhalb des exakt darstellbaren Bereichs ` +
                       `(±2^53) – Kennungen bitte als Zeichenkette in Anführungszeichen, ` +
                       `große Zahlenwerte als Gleitkommazahl (z.B. 1.0e+20) schreiben`);
      return v;
    }
    if (RE_FLOAT.test(raw)) {
      const low = raw.toLowerCase();
      if (low.endsWith(".inf")) return low.startsWith("-") ? -Infinity : Infinity;
      if (low === ".nan") return NaN;
      return Number(raw);
    }
    return raw;
  }

  const ESC = {"0": "\0", "a": "\x07", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v",
               "f": "\f", "r": "\r", "e": "\x1b", " ": " ", "\"": "\"", "/": "/", "\\": "\\",
               "N": "\x85", "_": "\xa0", "L": "\u2028", "P": "\u2029"};
  const HEXLEN = {"x": 2, "u": 4, "U": 8};

  function readDouble(p) {                   // p.s[p.i] === '"'
    let out = "";
    for (let i = p.i + 1; i < p.s.length; i++) {
      const ch = p.s[i];
      if (ch === "\"") {
        // \ud83d\ude00 ergibt ein Zeichen (Paar); ein einzelnes Surrogat ist kein Zeichen
        if (/[\uD800-\uDFFF]/u.test(out))
          throw fail(p.no, "Zeichenkette mit einzelnem Surrogat (\\ud800…\\udfff) – ein " +
                           "unvollständiges Zeichen; Zeichen außerhalb der BMP als Paar " +
                           "(\"\\ud83d\\ude00\") oder direkt schreiben");
        p.i = i + 1;
        return out;
      }
      if (ch !== "\\") { out += ch; continue; }
      const e = p.s[++i];
      if (e in ESC) { out += ESC[e]; continue; }
      if (e in HEXLEN) {
        const hex = p.s.slice(i + 1, i + 1 + HEXLEN[e]);
        if (!/^[0-9a-fA-F]+$/.test(hex) || hex.length !== HEXLEN[e])
          throw fail(p.no, `ungültige Escape-Sequenz '\\${e}${hex}'`);
        out += String.fromCodePoint(parseInt(hex, 16));
        i += HEXLEN[e];
        continue;
      }
      throw fail(p.no, `ungültige Escape-Sequenz '\\${e === undefined ? "" : e}'`);
    }
    throw unsupported(p.no, "Ein nicht geschlossener bzw. mehrzeiliger String in \"…\"");
  }

  function readSingle(p) {                   // p.s[p.i] === "'"
    let out = "";
    for (let i = p.i + 1; i < p.s.length; i++) {
      if (p.s[i] !== "'") { out += p.s[i]; continue; }
      if (p.s[i + 1] === "'") { out += "'"; i++; continue; }
      p.i = i + 1;
      return out;
    }
    throw unsupported(p.no, "Ein nicht geschlossener bzw. mehrzeiliger String in '…'");
  }

  // Leerraum: Leerzeichen bzw. "\n" (verbindet die Zeilen mehrzeiliger Flow-Werte;
  // CBRK steht zusätzlich nach einem Kommentar bzw. einer Leerzeile)
  const ws = c => c === " " || c === "\n" || c === CBRK || c === BBRK;
  const wsEnd = c => c === undefined || ws(c);

  // Rohtext eines ungequoteten Skalars; nachlaufender Leerraum bleibt stehen
  // (skipToColon prüft ihn), p.multiline: Skalar erstreckt sich über Zeilen
  function readPlain(p, inFlow) {
    const start = p.i;
    let i = p.i;
    for (; i < p.s.length; i++) {
      const ch = p.s[i];
      if (inFlow && ",[]{}".includes(ch)) break;
      if (ch === ":" && wsEnd(p.s[i + 1])) break;               // 'a:b', 'a:,' bleiben Text
    }
    while (i > start && ws(p.s[i - 1])) i--;
    const raw = p.s.slice(start, i);
    if (raw.includes(CBRK))                  // ein Kommentar beendet den Skalar (wie in yamlio)
      throw fail(p.no, `nach dem Kommentar läuft der Wert '${raw.slice(0, raw.indexOf("\n"))}' ` +
                       `in der Folgezeile weiter – fehlt ein ',' (oder steht die schließende ` +
                       `Klammer im Kommentar)?`);
    if (raw.includes(BBRK))
      throw unsupported(p.no, "Eine Leerzeile innerhalb eines mehrzeiligen Skalars");
    p.i = i;
    p.multiline = raw.includes("\n");
    return raw.replace(/ *\n */g, " ");                        // mehrzeilig → gefaltet
  }

  function skip(p) { while (ws(p.s[p.i])) p.i++; }

  // Implizite Schlüssel und ihr ':' müssen in derselben Zeile stehen (wie ruamel)
  function skipToColon(p, k) {
    const from = p.i;
    skip(p);
    if (p.s[p.i] === ":" && (k.multiline || p.s.slice(from, p.i).includes("\n")))
      throw fail(p.no, "Schlüssel und ':' müssen in derselben Zeile stehen");
  }

  // Indikatorzeichen am Anfang eines ungequoteten Skalars (wie ruamels check_plain)
  function checkIndicator(p, inFlow) {
    const ch = p.s[p.i], nx = p.s[p.i + 1];
    const nxEnd = wsEnd(nx);
    if (ch === "&" || ch === "*") throw unsupported(p.no, "Anker/Alias (&name, *name)");
    if (ch === "!") throw unsupported(p.no, "Ein Tag (!…)");
    if (ch === "|" || ch === ">") {
      if (!inFlow) throw unsupported(p.no, "Ein Block-Skalar (| oder >)");
      throw fail(p.no, `'${ch}' am Wertanfang in {…}/[…] – bitte quoten`);
    }
    if ("%@`".includes(ch)) throw fail(p.no, `reserviertes Zeichen '${ch}' am Wertanfang – bitte quoten`);
    if (ch === "?" && (inFlow || nxEnd))
      throw unsupported(p.no, "Ein expliziter/komplexer Schlüssel (? …)");
    if (ch === "-" && nxEnd)
      throw fail(p.no, "Listeneintrag '- ' ist an dieser Stelle nicht erlaubt");
    if (!inFlow && "],}".includes(ch)) throw fail(p.no, `'${ch}' am Wertanfang – bitte quoten`);
  }

  // Knoten in Flow-Kontext (inFlow) bzw. kompletter Inline-Wert (inFlow=false)
  function parseNode(p, inFlow) {
    skip(p);
    const ch = p.s[p.i];
    if (ch === "{") return parseFlowMap(p);
    if (ch === "[") return parseFlowSeq(p);
    if (ch === "\"") return readDouble(p);
    if (ch === "'") return readSingle(p);
    if (ch !== undefined) checkIndicator(p, inFlow);
    return resolvePlain(readPlain(p, inFlow), p.no);
  }

  // Schlüssel: Originaltext (gequotet → Inhalt), nie typaufgelöst
  function parseKeyText(p, inFlow, emptyOk = false) {
    skip(p);
    const ch = p.s[p.i];
    if (emptyOk && ch === ":") return {key: "", quoted: false};   // {: 6} → Schlüssel "" (wie ruamel)
    if (ch === "\"") return {key: readDouble(p), quoted: true};
    if (ch === "'") return {key: readSingle(p), quoted: true};
    if (ch === "{" || ch === "[") throw unsupported(p.no, "Eine Liste bzw. ein Mapping als Schlüssel");
    if (ch !== undefined) checkIndicator(p, inFlow);
    const key = readPlain(p, inFlow);
    return {key, quoted: false, multiline: p.multiline};
  }

  function setKey(obj, k, v, no) {
    if (k.key === "<<" && !k.quoted)
      throw fail(no, "Merge-Schlüssel '<<' gehört nicht zu YAML 1.2 – bitte die Werte ausschreiben");
    if (k.key === "__proto__") throw fail(no, "Schlüssel '__proto__' ist nicht erlaubt");
    if (Object.prototype.hasOwnProperty.call(obj, k.key))
      throw fail(no, `Doppelter Schlüssel '${k.key}' – bitte eindeutig benennen`);
    obj[k.key] = v;
  }

  function parseFlowMap(p) {                 // p.s[p.i] === "{"
    p.i++;
    const obj = {};
    for (;;) {
      skip(p);
      if (p.s[p.i] === "}") { p.i++; return obj; }
      if (p.i >= p.s.length) throw fail(p.no, "'}' fehlt");
      if (p.s[p.i] === ",") throw fail(p.no, "leerer Eintrag in {…}");
      const k = parseKeyText(p, true, true);
      skipToColon(p, k);
      let v = null;
      if (p.s[p.i] === ":") {
        p.i++;
        skip(p);
        // ruamel liest ':x' hier je nach Token-Puffer als Text oder als Fehler
        // (Implementierungsartefakt) — der Editor lehnt eindeutig ab
        if (p.s[p.i] === ":") throw unsupported(p.no, "Ein ':' am Wertanfang in {…} (bitte quoten)");
        v = (p.s[p.i] === "," || p.s[p.i] === "}") ? null : parseNode(p, true);
      }
      setKey(obj, k, v, p.no);
      skip(p);
      if (p.s[p.i] === ",") { p.i++; continue; }
      if (p.s[p.i] === "}") { p.i++; return obj; }
      throw fail(p.no, `',' oder '}' erwartet bei '${p.s.slice(p.i, p.i + 20)}'`);
    }
  }

  function parseFlowSeq(p) {                 // p.s[p.i] === "["
    p.i++;
    const arr = [];
    for (;;) {
      skip(p);
      if (p.s[p.i] === "]") { p.i++; return arr; }
      if (p.i >= p.s.length) throw fail(p.no, "']' fehlt");
      if (p.s[p.i] === ",") throw fail(p.no, "leeres Listenelement");
      const ch = p.s[p.i];
      if (ch === ":" && wsEnd(p.s[p.i + 1]))
        throw fail(p.no, "': ' am Anfang eines Listenelements – bitte quoten");
      let item;
      if (ch === "{" || ch === "[") item = parseNode(p, true);
      else {                                 // Skalar – oder Einzelpaar 'a: 1'
        const save = p.i;
        const k = parseKeyText(p, true);
        skipToColon(p, k);
        if (p.s[p.i] === ":" && wsEnd(p.s[p.i + 1])) {
          p.i++;
          skip(p);
          const v = (p.s[p.i] === "," || p.s[p.i] === "]") ? null : parseNode(p, true);
          item = {};
          setKey(item, k, v, p.no);
        } else {
          p.i = save;
          item = parseNode(p, true);
        }
      }
      arr.push(item);
      skip(p);
      if (p.s[p.i] === ",") { p.i++; continue; }
      if (p.s[p.i] === "]") { p.i++; return arr; }
      throw fail(p.no, `',' oder ']' erwartet bei '${p.s.slice(p.i, p.i + 20)}'`);
    }
  }

  function parseInline(text, no) {           // kompletter Wert einer Zeile
    const p = {s: text, i: 0, no};
    const v = parseNode(p, false);
    skip(p);
    if (p.i < p.s.length) throw fail(no, `unerwarteter Text nach dem Wert: '${p.s.slice(p.i)}'`);
    return v;
  }

  /* --- Zeilen: Kommentare, Einrückung, mehrzeilige Flow-Werte ------------- */
  // Zeilenscanner mit ruamels Token-Regeln: entfernt Kommentare, bestimmt die
  // Flow-Klammertiefe (depth0 = Tiefe am Zeilenanfang bei Fortsetzungszeilen),
  // offene Quotes und Tabulatoren.
  // - Quote bzw. '[' '{' beginnen einen Knoten nur am Token-Anfang (Zeilen-
  //   anfang, nach ': ', '- ', '? ' oder einem Flow-Zeichen); sonst sind sie
  //   gewöhnlicher Text: it's, a[1], .{'
  // - '#' beginnt einen Kommentar nach Leerraum oder direkt nach einem
  //   Strukturzeichen (Flow-Klammer, Flow-Komma, Quote-Ende, ':' direkt nach
  //   einem Quote im Flow): "a"#x, [a]#x, {"a":#x
  function scanLine(s, depth0 = 0) {
    let depth = depth0, q = null, tab = false, edge = -2;   // edge: letztes Strukturzeichen
    const opens = i => {
      if (i === 0) return true;
      const prev = s[i - 1];
      if (i - 1 === edge && "[{,:".includes(prev)) return true;
      if (prev !== " ") return false;
      let j = i - 1;
      while (j >= 0 && s[j] === " ") j--;
      if (j < 0 || s[j] === ":") return true;
      if (j === edge && "[{,".includes(s[j])) return true;
      return "-?".includes(s[j]) && (j === 0 || s[j - 1] === " ");
    };
    for (let i = 0; i < s.length; i++) {
      const ch = s[i];
      if (q === "\"") { if (ch === "\\") i++; else if (ch === "\"") { q = null; edge = i; } continue; }
      if (q === "'") {
        if (ch === "'") { if (s[i + 1] === "'") i++; else { q = null; edge = i; } }
        continue;
      }
      if ((ch === "\"" || ch === "'") && opens(i)) { q = ch; continue; }
      if (ch === "#" && (i === 0 || s[i - 1] === " " || s[i - 1] === "\t" || i - 1 === edge))
        return {text: s.slice(0, i), depth: depth - depth0, q, tab, comment: true};
      if (ch === "\t") tab = true;
      if ((ch === "{" || ch === "[") && (depth > 0 || opens(i))) { depth++; edge = i; }
      else if ((ch === "}" || ch === "]") && depth > 0) { depth--; edge = i; }
      else if (ch === "," && depth > 0) edge = i;
      else if (ch === ":" && depth > 0 && i - 1 === edge && "\"'".includes(s[i - 1]))
        edge = i;                            // {"a":…}: ':' direkt nach Quote ist Struktur
    }
    return {text: s, depth: depth - depth0, q, tab, comment: false};
  }

  function logicalLines(text) {
    const raw = String(text).replace(/^\uFEFF/, "").split(/\r\n|\r|\n/);
    const out = [];
    for (let n = 0; n < raw.length; n++) {
      const no = n + 1;
      const lead = raw[n].match(/^[ \t]*/)[0];
      const sc = scanLine(raw[n]);
      // wie ruamel auch in Leer-/Kommentarzeilen: Tabulator nur in Strings und Kommentaren
      if (lead.includes("\t")) throw fail(no, "Tabulator in der Einrückung – YAML erlaubt nur Leerzeichen");
      if (sc.tab) throw unsupported(no, "Ein Tabulator außerhalb eines Strings (bitte Leerzeichen)");
      if (!trimSp(sc.text)) continue;
      const indent = lead.length;
      let body = trimSp(sc.text), depth = sc.depth;
      if (sc.q) throw unsupported(no, "Ein mehrzeiliger String");
      let brk = sc.comment ? CBRK : "";      // Kommentar beendet einen Skalar am Zeilenende
      while (depth > 0) {                    // mehrzeiliger Flow-Wert: Folgezeilen anhängen
        if (++n >= raw.length) throw fail(no, "Klammer '{' bzw. '[' wird nicht geschlossen");
        if (/^(?:---|\.\.\.)(?:[ \t]|$)/.test(raw[n]))
          throw fail(n + 1, "Dokumentmarke ('---' bzw. '...') innerhalb von {…}/[…]");
        const cont = scanLine(raw[n], depth);
        if (cont.q) throw unsupported(n + 1, "Ein mehrzeiliger String");
        if (cont.tab) throw unsupported(n + 1, "Ein Tabulator außerhalb eines Strings (bitte Leerzeichen)");
        const t = trimSp(cont.text);
        if (!t) {                            // Kommentar- bzw. Leerzeile: Skalar läuft hier nicht weiter
          brk = cont.comment ? CBRK : (brk || BBRK);
          continue;
        }
        if (t === "-" || t.startsWith("- "))
          throw fail(n + 1, `Zeile beginnt mit '- ', obwohl die in Zeile ${no} geöffnete Klammer ` +
                            `'[' bzw. '{' noch offen ist – fehlt die schließende Klammer (steht ` +
                            `sie in einem Kommentar)? Mehrzeilige Werte, deren Fortsetzung mit ` +
                            `'- ' beginnt, liest dieser Parser nicht`);
        body += "\n" + brk + t;
        depth += cont.depth;
        brk = cont.comment ? CBRK : "";
      }
      out.push({no, indent, text: body});
    }
    return out;
  }

  /* --- Block-Struktur ----------------------------------------------------------- */
  const isSeqItem = t => t === "-" || t.startsWith("- ");

  // Position des ':' eines Block-Mapping-Eintrags ('schlüssel: wert') oder -1
  function mapSep(t) {
    if (t[0] === "{" || t[0] === "[") return -1;
    let q = null;
    for (let i = 0; i < t.length; i++) {
      const ch = t[i];
      if (q === "\"") { if (ch === "\\") i++; else if (ch === "\"") q = null; continue; }
      if (q === "'") { if (ch === "'") { if (t[i + 1] === "'") i++; else q = null; } continue; }
      if ((ch === "\"" || ch === "'") && i === 0) { q = ch; continue; }
      if (ch === ":" && (i + 1 === t.length || ws(t[i + 1]))) return i;
    }
    return -1;
  }

  // Knoten ab Zeile i in Spalte indent; parent = Einrückung des Elternknotens
  function parseBlock(L, i, indent, parent = -1) {
    if (isSeqItem(L[i].text)) return parseSeq(L, i, indent);
    if (mapSep(L[i].text) >= 0) return parseMap(L, i, indent);
    const v = parseInline(L[i].text, L[i].no);
    if (i + 1 < L.length && L[i + 1].indent > parent)      // Fortsetzungszeile
      throw unsupported(L[i + 1].no, "Ein mehrzeiliger Wert");
    return [v, i + 1];
  }

  function childOf(L, i, indent) {            // Wert nach 'schlüssel:' bzw. '-' ohne Inline-Teil
    if (i < L.length && L[i].indent > indent) return parseBlock(L, i, L[i].indent, indent);
    return [null, i];
  }

  function parseMap(L, i, indent) {
    const obj = {};
    while (i < L.length && L[i].indent === indent && !isSeqItem(L[i].text)) {
      const ln = L[i], sep = mapSep(ln.text);
      if (sep < 0) throw fail(ln.no, `'schlüssel: wert' erwartet, erhalten: '${ln.text}'`);
      if (sep === 0) throw unsupported(ln.no, "Ein leerer Schlüssel (': …')");
      const kp = {s: trimEndSp(ln.text.slice(0, sep)), i: 0, no: ln.no};
      let k;
      if (kp.s[0] === "\"" || kp.s[0] === "'") {
        k = parseKeyText(kp, false);
        if (kp.i < kp.s.length) throw fail(ln.no, `ungültiger Schlüssel '${kp.s}'`);
      } else {                                // Klartext bis zum Trenner, z.B. 'x:' in 'x:: 1'
        checkIndicator({s: ln.text, i: 0, no: ln.no}, false);    // '-: 1' ist ein Schlüssel
        k = {key: kp.s, quoted: false};
      }
      const rest = ln.text.slice(sep + 1).replace(/^[ \n\u0001\u0002]+|[ \n\u0001\u0002]+$/g, "");
      let v;
      i++;
      if (rest === "") {
        if (i < L.length && L[i].indent === indent && isSeqItem(L[i].text)) [v, i] = parseSeq(L, i, indent);
        else [v, i] = childOf(L, i, indent);
      } else {
        v = parseInline(rest, ln.no);
        if (i < L.length && L[i].indent > indent) throw unsupported(L[i].no, "Ein mehrzeiliger Wert");
      }
      setKey(obj, k, v, ln.no);
    }
    return [obj, i];
  }

  function parseSeq(L, i, indent) {
    const arr = [];
    while (i < L.length && L[i].indent === indent && isSeqItem(L[i].text)) {
      const ln = L[i];
      const rest = ln.text.slice(1).replace(/^ +/, "");
      let v;
      if (rest === "") [v, i] = childOf(L, i + 1, indent);
      else if (isSeqItem(rest) || mapSep(rest) >= 0) {
        // kompakte Schachtelung '- - x' bzw. '- a: 1': virtuelle Zeile in der Inhaltsspalte
        const col = indent + (ln.text.length - rest.length);
        L[i] = {no: ln.no, indent: col, text: rest};
        [v, i] = parseBlock(L, i, col, indent);
      } else {
        v = parseInline(rest, ln.no);
        i++;
        if (i < L.length && L[i].indent > indent) throw unsupported(L[i].no, "Ein mehrzeiliger Wert");
      }
      arr.push(v);
    }
    return [arr, i];
  }

  function parse(text) {
    text = String(text);
    const bad = RE_CTRL.exec(text);
    if (bad) {
      const cp = bad[0].codePointAt(0).toString(16).toUpperCase().padStart(4, "0");
      const no = text.slice(0, bad.index).split(/\r\n|\r|\n/).length;
      throw fail(no, `Steuerzeichen U+${cp} ist als Rohzeichen nicht erlaubt – bitte entfernen ` +
                     `oder in einem String in doppelten Anführungszeichen als Escape schreiben ` +
                     `(z.B. "\\u${cp.toLowerCase()}")`);
    }
    const L = logicalLines(text);
    if (L.length && L[0].text === "---") L.shift();          // Dokumentanfang erlaubt
    for (const ln of L) {
      if (ln.text === "---" || ln.text.startsWith("--- ") || ln.text === "..." ||
          (ln.indent === 0 && ln.text.startsWith("... ")))
        throw fail(ln.no, "mehrere Dokumente ('---') – bitte genau ein Dokument je Datei");
      if (ln.text.startsWith("%")) throw unsupported(ln.no, "Eine YAML-Direktive (%…)");
    }
    if (!L.length) return null;
    const [v, i] = parseBlock(L, 0, L[0].indent);
    if (i < L.length) throw fail(L[i].no, "unerwartete Einrückung bzw. Zeile");
    return v;
  }

  /* --- Schreiben ------------------------------------------------------------------- */
  function number(v) {
    if (Number.isNaN(v)) return ".nan";
    if (v === Infinity) return ".inf";
    if (v === -Infinity) return "-.inf";
    if (Object.is(v, -0)) return "-0.0";                 // String(-0) wäre "0"
    // ganzzahlig, aber jenseits von 2^53: als Gleitkommazahl schreiben — die
    // Ziffernfolge von String(v) wäre eine ANDERE Ganzzahl (und wird beim Lesen abgelehnt)
    const s = Number.isInteger(v) && Math.abs(v) > Number.MAX_SAFE_INTEGER
      ? v.toExponential() : String(v);
    const m = s.match(/^(-?\d+)(\.\d+)?e([+-]?)(\d+)$/);
    return m ? `${m[1]}${m[2] || ".0"}e${m[3] || "+"}${m[4]}` : s;
  }

  function plainIsString(s) {
    try { return typeof resolvePlain(s) === "string"; } catch (e) { return false; }
  }
  function string(v) {
    // ein einzelnes Surrogat ist kein Zeichen (beide Parser lehnen es ab) → U+FFFD
    const s = String(v).replace(/[\uD800-\uDFFF]/gu, "\uFFFD");
    if (/^[A-Za-z_][A-Za-z0-9_.\-]*$/.test(s) && plainIsString(s) &&
        !YAML11_WORDS.has(s.toLowerCase())) return s;
    // JSON-String = gültiger YAML-Doppelquote-String (C0 und einzelne Surrogate
    // escapt JSON.stringify); übrige nicht druckbare Zeichen ebenfalls escapen
    return JSON.stringify(s).replace(/[\u007f-\u009f\u2028\u2029\uFEFF\uFFFE\uFFFF]/g,
      c => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
  }

  function scalar(v) {
    if (typeof v === "number") return number(v);
    if (typeof v === "boolean") return v ? "true" : "false";
    if (v === null || v === undefined) return "null";
    if (Array.isArray(v)) return `[${v.map(scalar).join(", ")}]`;
    if (typeof v === "object")
      return `{${Object.entries(v).map(([k, x]) => `${string(k)}: ${scalar(x)}`).join(", ")}}`;
    return string(v);
  }

  return {parse, resolvePlain, scalar, string, number};
})();
if (typeof module !== "undefined" && module.exports) module.exports = YamlCore;
