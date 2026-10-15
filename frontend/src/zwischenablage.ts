// navigator.clipboard gibt es nur über HTTPS oder localhost. Über http:// im
// Heimnetz bleibt der alte Weg über ein unsichtbares Textfeld.
export async function inZwischenablage(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // weiter mit dem alten Weg
  }
  const feld = document.createElement("textarea");
  feld.value = text;
  feld.setAttribute("readonly", "");
  feld.style.position = "fixed";
  feld.style.opacity = "0";
  document.body.appendChild(feld);
  feld.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    feld.remove();
  }
}
