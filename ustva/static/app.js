"use strict";

const $ = (sel) => document.querySelector(sel);
const fmt = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtGanz = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });
let steuerfaelle = [];
let status = null;
let belegeImZeitraum = [];

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function datum(iso) {
  if (!iso) return "–";
  const [j, m, t] = iso.split("-");
  return `${t}.${m}.${j}`;
}

function zuDezimal(text) {
  const t = String(text ?? "").trim().replace(/\s/g, "");
  if (t === "") return null;
  return t.includes(",") ? t.replace(/\./g, "").replace(",", ".") : t;
}

function alsEingabe(wert) {
  return wert == null ? "" : String(wert).replace(".", ",");
}

async function api(pfad, optionen = {}) {
  const antwort = await fetch(pfad, {
    headers: { "Content-Type": "application/json" },
    ...optionen,
  });
  if (!antwort.ok) {
    let text = `Fehler ${antwort.status}`;
    try {
      const daten = await antwort.json();
      if (typeof daten.detail === "string") text = daten.detail;
      else if (Array.isArray(daten.detail)) text = daten.detail.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join("; ");
    } catch (_) { /* keine JSON-Antwort */ }
    throw new Error(text);
  }
  return antwort.status === 204 ? null : antwort.json();
}

function meldung(text, fehler = false) {
  const el = $("#meldung");
  el.textContent = text;
  el.classList.toggle("fehler", fehler);
  el.hidden = !text;
}

function zeitraum() {
  return $("#zeitraum").value;
}

async function start() {
  [status, steuerfaelle] = await Promise.all([api("/api/status"), api("/api/steuerfaelle")]);
  const f = status.frist;
  const frist = $("#frist");
  frist.innerHTML = `<span class="klein">Nächste Abgabe</span><strong>UStVA ${esc(f.zeitraum_text)}</strong>` +
    `bis ${esc(f.faellig_text)} · ${f.tage_bis === 0 ? "heute" : `noch ${f.tage_bis} Tag${f.tage_bis === 1 ? "" : "e"}`}`;
  frist.classList.toggle("dringend", f.tage_bis <= 3);
  frist.hidden = false;
  $("#versteuerung").textContent = `${status.versteuerung === "ist" ? "Ist" : "Soll"}-Versteuerung` +
    (status.dauerfristverlaengerung ? ", mit Dauerfristverlängerung" : "");
  $("#zeitraum").value = f.zeitraum;
  const quellen = [status.verzeichnis && "Verzeichnis", status.paperless && "Paperless"].filter(Boolean);
  $("#einlesen").disabled = quellen.length === 0;
  $("#einlesen").title = quellen.length ? `Aus ${quellen.join(" und ")} einlesen` : "Keine Quelle konfiguriert";
  await laden();
}

async function laden() {
  const zr = zeitraum();
  if (!zr) return;
  $("#export-kennzahlen").href = `/api/export/${zr}/kennzahlen.csv`;
  $("#export-belege").href = `/api/export/${zr}/belege.csv`;
  const [bericht, offen, kurse] = await Promise.all([api(`/api/bericht/${zr}`), api("/api/offen"), api("/api/kurse")]);
  zeigeKennzahlen(bericht);
  zeigeHinweise(bericht);
  belegeImZeitraum = bericht.belege;
  zeigeBelege($("#belege"), bericht.belege);
  $("#keine-belege").hidden = bericht.belege.length > 0;
  zeigeBelege($("#offen"), offen);
  $("#offen-karte").hidden = offen.length === 0;
  zeigeKurse(kurse);
  zeigeUmsaetze(bericht.umsaetze ?? []);
}

function zeigeUmsaetze(umsaetze) {
  $("#keine-umsaetze").hidden = umsaetze.length > 0;
  $("#umsaetze").innerHTML = umsaetze.map((u) => {
    const betrag = Number(u.betrag);
    const original = u.original_betrag && u.original_waehrung && u.original_waehrung !== "EUR"
      ? `<div class="klein">${fmt.format(Math.abs(Number(u.original_betrag)))} ${esc(u.original_waehrung)}</div>` : "";
    let status;
    let aktionen;
    if (u.beleg) {
      const b = u.beleg;
      status = `<span class="ok">✓</span> ${esc(b.partner || b.beschreibung || "Beleg")}${b.rechnungsnummer ? ` · ${esc(b.rechnungsnummer)}` : ""}`;
      aktionen = `<button class="klein" data-loesen="${u.id}">Lösen</button>`;
    } else if (u.ignoriert) {
      status = `<span class="klein">kein Beleg nötig${u.ignoriert_grund ? ` (${esc(u.ignoriert_grund)})` : ""}</span>`;
      aktionen = `<button class="klein" data-rueckgaengig="${u.id}">Rückgängig</button>`;
    } else {
      status = `<strong class="fehler">Beleg fehlt</strong>`;
      aktionen = `<select class="klein" data-zuordnen="${u.id}"><option value="">Beleg zuordnen …</option></select>
        <button class="klein" data-ignorieren="${u.id}">Kein Beleg nötig</button>
        ${u.partner ? `<button class="klein" data-regel="${u.id}" title="Alle Umsätze mit ${esc(u.partner)} ohne Beleg akzeptieren">Partner immer</button>` : ""}`;
    }
    return `<tr class="${u.beleg || u.ignoriert ? "erledigt" : "fehlt"}">
      <td>${datum(u.buchungsdatum)}</td>
      <td>${esc(u.partner || "–")}<div class="klein">${esc(u.verwendungszweck || u.typ)}</div></td>
      <td class="zahl">${betrag > 0 ? "+" : ""}${fmt.format(betrag)} €${original}</td>
      <td>${status}</td>
      <td><div class="aktionen-zelle">${aktionen}</div></td>
    </tr>`;
  }).join("");
}

async function kandidatenLaden(select) {
  if (select.dataset.geladen) return;
  select.dataset.geladen = "1";
  const liste = await api(`/api/umsaetze/${select.dataset.zuordnen}/kandidaten`);
  select.insertAdjacentHTML("beforeend", liste.length ? liste.map((b) =>
    `<option value="${b.id}">${datum(b.rechnungsdatum)} · ${esc(b.partner || b.beschreibung || "Beleg")} · ` +
    `${fmt.format(Number(b.zahlbetrag))} ${esc(b.waehrung)}</option>`).join("")
    : `<option disabled>Keine passenden Belege</option>`);
}

async function bankHochladen(datei) {
  meldung("Kontoumsätze werden eingelesen …");
  try {
    const text = await datei.text();
    const e = await api("/api/import/bank", { method: "POST", headers: { "Content-Type": "text/csv" }, body: text });
    meldung([`Konto: ${e.neu} neu, ${e.vorhanden} schon vorhanden, ${e.zugeordnet} Belegen zugeordnet`, ...e.fehler].join(" | "),
      e.fehler.length > 0);
    await laden();
  } catch (err) {
    meldung(err.message, true);
  }
}

function elsterWert(k) {
  const betrag = Number(k.betrag);
  return k.betrag_art === "bemessungsgrundlage" ? String(Math.trunc(betrag)) : betrag.toFixed(2).replace(".", ",");
}

function zeigeKennzahlen(bericht) {
  const zeilen = bericht.kennzahlen.filter((k) => k.kz !== "83");
  $("#kennzahlen").innerHTML = zeilen.length ? zeilen.map((k) => {
    const betrag = Number(k.betrag);
    const anzeige = k.betrag_art === "bemessungsgrundlage" ? `${fmtGanz.format(betrag)} €` : `${fmt.format(betrag)} €`;
    const kopieren = k.von_elster_berechnet ? `<span class="klein">berechnet ELSTER</span>`
      : `<button class="klein" data-kopie="${esc(elsterWert(k))}" title="Wert für ELSTER kopieren">Kopieren</button>`;
    return `<tr class="${k.ist_steuerzeile ? "steuerzeile" : ""}">
      <td>${k.ist_steuerzeile ? "" : esc(k.kz)}</td><td>${esc(k.titel)}</td>
      <td class="zahl betrag">${anzeige}</td><td>${kopieren}</td></tr>`;
  }).join("") : `<tr><td colspan="4" class="klein">Keine Werte. Alle Kennzahlen bleiben leer (Nullmeldung).</td></tr>`;

  const zahllast = Number(bericht.zahllast);
  $("#zahllast").innerHTML = `<div><strong>Kz 83</strong> ${zahllast < 0 ? "Überschuss (Erstattung)" : "Zahllast"}
      <div class="klein">Umsatzsteuer ${fmt.format(Number(bericht.umsatzsteuer))} € − Vorsteuer ${fmt.format(Number(bericht.vorsteuer))} €</div></div>
    <div class="wert">${fmt.format(zahllast)} €</div>`;
}

function zeigeHinweise(bericht) {
  const hinweise = [...bericht.hinweise];
  if (!bericht.vollstaendig) hinweise.unshift({ stufe: "warnung", text: "Die Werte sind unvollständig." });
  $("#hinweise").innerHTML = hinweise.length
    ? hinweise.map((h) => `<li class="${esc(h.stufe)}">${esc(h.text)}</li>`).join("")
    : `<li class="info">Keine Auffälligkeiten.</li>`;
  $("#zm").hidden = bericht.zm.length === 0;
  $("#zm-zeilen").innerHTML = bericht.zm.map((z) =>
    `<tr><td>${esc(z.ustid || "fehlt")}</td><td>${esc(z.art)}</td><td class="zahl">${fmtGanz.format(Number(z.betrag))}</td></tr>`).join("");
}

function steuerfallTitel(wert) {
  return steuerfaelle.find((s) => s.wert === wert)?.titel ?? wert;
}

function zeigeBelege(ziel, belege) {
  ziel.innerHTML = belege.map((b) => {
    const fremd = b.waehrung !== "EUR";
    const steuer = b.steuer_eur ?? b.steuer;
    const marken = [
      b.pruefen ? `<span class="marke pruefen">prüfen</span>` : "",
      b.quelle !== "manuell" ? `<span class="marke">${esc(b.quelle)}</span>` : "",
    ].join(" ");
    return `<tr data-id="${b.id}" class="${b.pruefen ? "pruefen" : ""} ${b.fehler ? "fehlerhaft" : ""}">
      <td>${datum(b.rechnungsdatum)}${b.zahlungsdatum ? `<div class="klein">bez. ${datum(b.zahlungsdatum)}</div>` : ""}</td>
      <td>${b.art === "ausgang" ? "Ausgang" : "Eingang"}</td>
      <td>${esc(b.partner || b.beschreibung || "–")} ${marken}
        ${b.partner && b.beschreibung ? `<div class="klein">${esc(b.beschreibung)}</div>` : ""}
        ${b.fehler ? `<div class="fehler klein">${esc(b.fehler)}</div>` : ""}</td>
      <td>${esc(steuerfallTitel(b.steuerfall))}<div class="klein">Kz ${esc(b.kennzahlen ?? "")}</div></td>
      <td class="zahl">${fmt.format(Number(b.netto))} ${esc(b.waehrung)}<div class="klein">${fmt.format(Number(b.steuersatz))} %</div></td>
      <td class="zahl">${steuer != null ? fmt.format(Number(steuer)) : "–"}</td>
      <td class="zahl">${b.netto_eur != null ? fmt.format(Number(b.netto_eur)) + " €" : "–"}
        ${fremd && b.kurs_verwendet ? `<div class="klein">Kurs ${esc(b.kurs_verwendet)}</div>` : ""}</td>
      <td>${b.pruefen ? `<button class="klein" data-geprueft="${b.id}">OK</button>` : ""}
        ${b.quelle_link && b.quelle === "paperless" ? `<a class="klein" href="${esc(b.quelle_link)}" target="_blank" rel="noopener">öffnen</a>` : ""}
        ${b.quelle === "verzeichnis" ? `<a class="klein" href="/api/belege/${b.id}/datei" target="_blank">öffnen</a>` : ""}</td>
    </tr>`;
  }).join("");
}

function zeigeKurse(kurse) {
  $("#kurse").innerHTML = kurse.map((k) => `<tr><td>${esc(k.zeitraum)}</td><td>${esc(k.waehrung)}</td>
    <td class="zahl">${esc(alsEingabe(k.kurs))}</td><td>${esc(k.quelle)}</td>
    <td><button class="klein" data-kurs-loeschen="${esc(k.waehrung)}/${esc(k.zeitraum)}">Entfernen</button></td></tr>`).join("");
}

// Dialog
let aktuellerBeleg = null;

function steuerfallOptionen(art, gewaehlt) {
  const sel = $("#beleg-form").steuerfall;
  sel.innerHTML = steuerfaelle.filter((s) => s.art === art || s.art === null)
    .map((s) => `<option value="${s.wert}">${esc(s.titel)} (Kz ${esc(s.kennzahlen)})</option>`).join("");
  if (gewaehlt && [...sel.options].some((o) => o.value === gewaehlt)) sel.value = gewaehlt;
}

function oeffneDialog(beleg) {
  aktuellerBeleg = beleg;
  const form = $("#beleg-form");
  form.reset();
  $("#dialog-titel").textContent = beleg ? "Beleg prüfen und bearbeiten" : "Beleg hinzufügen";
  $("#loeschen").hidden = !beleg;
  $("#dialog-fehler").hidden = true;
  const b = beleg ?? { art: "eingang", steuerfall: "vorsteuer_inland", waehrung: "EUR", steuersatz: "19",
    rechnungsdatum: `${zeitraum()}-01` };
  form.art.value = b.art;
  steuerfallOptionen(b.art, b.steuerfall);
  for (const feld of ["rechnungsdatum", "zahlungsdatum", "partner", "partner_ustid", "rechnungsnummer", "beschreibung", "waehrung"]) {
    form[feld].value = b[feld] ?? "";
  }
  for (const feld of ["netto", "steuersatz", "steuer", "kurs"]) form[feld].value = alsEingabe(b[feld]);
  $("#dialog-hinweise").innerHTML = (b.hinweise ?? []).map((h) => `<li class="warnung">${esc(h)}</li>`).join("");
  $("#dialog").showModal();
}

async function speichern(event) {
  event.preventDefault();
  const form = $("#beleg-form");
  const daten = {
    art: form.art.value,
    steuerfall: form.steuerfall.value,
    rechnungsdatum: form.rechnungsdatum.value || null,
    zahlungsdatum: form.zahlungsdatum.value || null,
    partner: form.partner.value,
    partner_ustid: form.partner_ustid.value.replace(/\s/g, "").toUpperCase(),
    rechnungsnummer: form.rechnungsnummer.value,
    beschreibung: form.beschreibung.value,
    waehrung: form.waehrung.value,
    netto: zuDezimal(form.netto.value),
    steuersatz: zuDezimal(form.steuersatz.value),
    steuer: zuDezimal(form.steuer.value),
    kurs: zuDezimal(form.kurs.value),
    pruefen: false,
  };
  try {
    if (aktuellerBeleg) await api(`/api/belege/${aktuellerBeleg.id}`, { method: "PUT", body: JSON.stringify(daten) });
    else await api("/api/belege", { method: "POST", body: JSON.stringify(daten) });
    $("#dialog").close();
    await laden();
  } catch (e) {
    $("#dialog-fehler").textContent = e.message;
    $("#dialog-fehler").hidden = false;
  }
}

async function einlesen() {
  const knopf = $("#einlesen");
  knopf.disabled = true;
  meldung("Rechnungen werden eingelesen …");
  try {
    const ergebnis = await api("/api/import", { method: "POST", body: JSON.stringify({ quelle: "alle", zeitraum: zeitraum() }) });
    const teile = [];
    let fehler = [];
    const namen = { paperless: "Paperless", verzeichnis: "Verzeichnis", konto: "Konto" };
    for (const [quelle, e] of Object.entries(ergebnis)) {
      if (quelle !== "keine") {
        teile.push(`${namen[quelle]}: ${e.neu} neu, ${e.vorhanden} schon vorhanden` +
          (e.zugeordnet ? `, ${e.zugeordnet} zugeordnet` : ""));
      }
      fehler = fehler.concat(e.fehler);
    }
    meldung([teile.join(" · "), ...fehler].filter(Boolean).join(" | "), fehler.length > 0);
    await laden();
  } catch (e) {
    meldung(e.message, true);
  } finally {
    knopf.disabled = false;
  }
}

document.addEventListener("click", async (event) => {
  const ziel = event.target;
  if (ziel.dataset.kopie !== undefined) {
    await navigator.clipboard?.writeText(ziel.dataset.kopie);
    ziel.textContent = "Kopiert";
    setTimeout(() => { ziel.textContent = "Kopieren"; }, 1200);
    return;
  }
  if (ziel.dataset.geprueft) {
    event.stopPropagation();
    await api(`/api/belege/${ziel.dataset.geprueft}/geprueft`, { method: "POST" });
    await laden();
    return;
  }
  if (ziel.dataset.kursLoeschen) {
    await api(`/api/kurse/${ziel.dataset.kursLoeschen}`, { method: "DELETE" });
    await laden();
    return;
  }
  const umsatzAktion = async (pfad, daten) => {
    try {
      await api(pfad, { method: "POST", body: JSON.stringify(daten) });
      await laden();
    } catch (e) {
      meldung(e.message, true);
    }
  };
  if (ziel.dataset.loesen) return umsatzAktion(`/api/umsaetze/${ziel.dataset.loesen}/zuordnen`, { beleg_id: null });
  if (ziel.dataset.ignorieren) return umsatzAktion(`/api/umsaetze/${ziel.dataset.ignorieren}/ignorieren`, {});
  if (ziel.dataset.rueckgaengig) {
    return umsatzAktion(`/api/umsaetze/${ziel.dataset.rueckgaengig}/ignorieren`, { ignoriert: false });
  }
  if (ziel.dataset.regel) {
    return umsatzAktion(`/api/umsaetze/${ziel.dataset.regel}/ignorieren`, { regel: true, grund: "Partner ohne Beleg" });
  }
  if (ziel.closest("a")) return;
  const zeile = ziel.closest("tr[data-id]");
  if (zeile) {
    const id = Number(zeile.dataset.id);
    const beleg = belegeImZeitraum.find((b) => b.id === id) ?? (await api("/api/offen")).find((b) => b.id === id);
    if (beleg) oeffneDialog(beleg);
  }
});

document.addEventListener("focusin", (event) => {
  if (event.target.dataset?.zuordnen) kandidatenLaden(event.target).catch((e) => meldung(e.message, true));
});
document.addEventListener("change", async (event) => {
  const sel = event.target;
  if (!sel.dataset?.zuordnen || !sel.value) return;
  try {
    await api(`/api/umsaetze/${sel.dataset.zuordnen}/zuordnen`, { method: "POST", body: JSON.stringify({ beleg_id: Number(sel.value) }) });
    await laden();
  } catch (e) {
    meldung(e.message, true);
  }
});
$("#bank-datei").addEventListener("change", (event) => {
  const datei = event.target.files[0];
  event.target.value = "";
  if (datei) bankHochladen(datei);
});
$("#zeitraum").addEventListener("change", () => laden().catch((e) => meldung(e.message, true)));
$("#einlesen").addEventListener("click", einlesen);
$("#neu").addEventListener("click", () => oeffneDialog(null));
$("#abbrechen").addEventListener("click", () => $("#dialog").close());
$("#beleg-form").addEventListener("submit", speichern);
$("#beleg-form").art.addEventListener("change", (e) => steuerfallOptionen(e.target.value));
$("#loeschen").addEventListener("click", async () => {
  if (!aktuellerBeleg || !confirm("Beleg wirklich löschen?")) return;
  await api(`/api/belege/${aktuellerBeleg.id}`, { method: "DELETE" });
  $("#dialog").close();
  await laden();
});
$("#kurs-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const f = event.target;
  try {
    await api("/api/kurse", { method: "PUT", body: JSON.stringify({
      waehrung: f.waehrung.value, zeitraum: f.zeitraum.value, kurs: zuDezimal(f.kurs.value), quelle: "BMF" }) });
    f.reset();
    await laden();
  } catch (e) {
    meldung(e.message, true);
  }
});

start().catch((e) => meldung(`Start fehlgeschlagen: ${e.message}`, true));
