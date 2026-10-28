/**
 * Receptor del registro de consultas del Monitor Calendario Trendy.
 * Pegar en: Google Sheets → Extensiones → Apps Script → Código.gs
 * Implementar como Aplicación web (Ejecutar como: yo · Quién tiene acceso: cualquier usuario).
 */

// Debe ser IGUAL a la variable SHEETS_TOKEN que pongas en Railway.
const TOKEN = 'CAMBIA-ESTA-CLAVE';
const HOJA = 'Consultas';

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const body = JSON.parse(e.postData.contents);
    if (body.token !== TOKEN) return respuesta({ ok: false, error: 'token incorrecto' });

    const libro = SpreadsheetApp.getActiveSpreadsheet();
    const hoja = libro.getSheetByName(HOJA) || libro.insertSheet(HOJA);

    if (hoja.getLastRow() === 0) {
      hoja.appendRow(body.headers);
      hoja.setFrozenRows(1);
      hoja.getRange(1, 1, 1, body.headers.length)
        .setFontWeight('bold').setBackground('#d6336c').setFontColor('#ffffff').setWrap(true);
    }

    const filas = body.rows || [];
    if (filas.length) {
      hoja.getRange(hoja.getLastRow() + 1, 1, filas.length, filas[0].length).setValues(filas);
    }
    return respuesta({ ok: true, agregadas: filas.length });
  } catch (err) {
    return respuesta({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

// Abre la URL de la aplicación web en el navegador para comprobar que está publicada.
function doGet() {
  return respuesta({ ok: true, mensaje: 'Receptor del monitor Trendy activo' });
}

function respuesta(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
