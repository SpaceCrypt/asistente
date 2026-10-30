# Asistente personal



Asistente local en español. Corre solo en este PC, en http://127.0.0.1:8765. Puede mirar archivos, preparar cambios y redactar correos. No escribe, no borra, no mueve y no envía nada hasta que pulses **Aprobar**.

El código está separado:

- `back/` es el servidor FastAPI, el agente, las herramientas y el acceso a los datos.
- `front/` es la pantalla: `index.html`, `estilos.css` y `app.js`.
- `datos/` guarda lo local (chats, memoria, pendientes, actividad). No es código.

La tipografía de la pantalla es Segoe UI, system-ui, sans-serif.

## Instalar y arrancar

Hace falta Python 3.12 y [Ollama](https://ollama.com) en marcha. Doble clic en `iniciar.bat`. Busca Python en tu usuario, crea `venv` si falta, instala las dependencias y arranca el servidor.

En PowerShell, desde esta carpeta:

```bat
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
```

```powershell
$env:PYTHONUTF8 = "1"
.\venv\Scripts\python.exe -m uvicorn back.main:app --host 127.0.0.1 --port 8765
```

Abre http://127.0.0.1:8765. Para pararlo, cierra esa ventana o pulsa Ctrl+C. Si el puerto está ocupado, cierra la ventana anterior del asistente y vuelve a arrancar. El arranque no carga ningún modelo en la gráfica.

## El archivo .env

Crea un archivo llamado `.env` en esta carpeta (al lado de `iniciar.bat`). `.env` está en `.gitignore`: no se sube a GitHub y no hay que pegar secretos en el código ni en el README.

Ejemplo, sin claves reales. `ollama` es un valor ficticio: Ollama no pide una clave.

```
ASISTENTE_API_KEY=ollama
ASISTENTE_BASE_URL=http://127.0.0.1:11434/v1
ASISTENTE_MODELO=qwen3:8b
ASISTENTE_MODO=ram
ASISTENTE_NUM_CTX=2048
ASISTENTE_KEEP_ALIVE=30s
```

`ASISTENTE_MODO=ram` es el valor si todavía no hay una elección guardada en `datos/ajustes.json`. Si ya guardaste Solo gráfica, el programa respeta ese archivo. Después de cambiar `.env` hay que reiniciar. La elección de la pantalla se guarda en `datos/ajustes.json` y manda sobre el arranque.

Para usar otro servicio compatible, cambia `.env` y reinicia. Con un servicio externo, el mensaje sale de este PC. La clave solo va en `.env`.

## Modelos y consumo

En este equipo, `ollama list` tiene dos modelos. No hay más instalados.

| Modelo | En disco | Al cargarse | Etiqueta |
| --- | --- | --- | --- |
| qwen2.5:7b | 4,7 GB | unos 4,7 GB de pesos | El que menos consume |
| qwen3:8b | 5,2 GB | unos 5,2 GB de pesos | El que más consume |

La gráfica es una AMD Radeon RX 6600 de 8 GB. Ollama la usa por Vulkan. Un 8B al 100 % de la gráfica (99 capas y contexto 8192) llegó a colgar Windows. Por eso el contexto se queda en 2048 y ninguna opción manda 99 capas.

En la barra izquierda hay dos listas desplegables. Al cambiar, se guarda en `datos/ajustes.json`. Si cambias de modelo, el anterior se suelta con `keep_alive` 0 y sin generar texto. Elegir un modelo no lo carga: se carga en el siguiente mensaje.

## Modos de recursos

- **Solo RAM.** `num_gpu` 0. No toca la gráfica. Es el modo por defecto si no hay elección guardada.
- **Gráfica y RAM.** 12 capas en la gráfica, nunca más de 16, y el resto en memoria. Contexto 2048.
- **Solo gráfica.** Se puede elegir con cualquier modelo, también con qwen2.5:7b y qwen3:8b. Manda todas las capas a la gráfica. Si el modelo es grande, la pantalla avisa: «Puede llenar la gráfica.» El contexto sigue en 2048.

El modelo se suelta a los 30 segundos.

## Qué puedes pedirle

Háblale con tus palabras. Entiende una frase con varias acciones y un mensaje que continúa el anterior.

- Listar y leer el Escritorio, Documentos, Descargas y esta carpeta del proyecto. Eso se hace al momento.
- Crear una carpeta, un archivo de texto, un Word (.docx), un Excel (.xlsx) o un PDF. Escribir, borrar, mover y enviar esperan **Aprobar**.
- Redactar un correo. Sin Gmail, al aprobar se guarda en `datos/borradores.json`.
- Ver tareas locales y crear una.

Cuando nace una acción pendiente hay un aviso de Windows y una barra fija arriba de la pantalla, con el resumen y los botones Aprobar y Rechazar. Es una alerta por acción nueva, no en cada consulta.

## Limpiar antes de subir a GitHub

En **Datos locales**:

- **Limpiar pendientes** vacía `datos/pendientes.json`.
- **Limpiar actividad** vacía el registro de actividad y el estado del último turno. No borra la memoria.
- **Olvidar lo aprendido** es aparte y sí vacía `datos/memoria.json`.

No borra el código. `.gitignore` deja fuera `.env`, `credentials.json`, `token.json`, la memoria, los chats, los pendientes, el registro, los ajustes y la carpeta `datos/` de secretos. Revisa igual que no quede un secreto en un archivo que sí se sube.

## Memoria

Lo aprendido se guarda en `datos/memoria.json`: preferencias, correcciones y, en una frase, lo que aprobaste o rechazaste. El siguiente turno solo recibe lo que encaja con lo que acabas de escribir. No pide aprobación y no sale del PC. No guarda contraseñas ni tokens. Si aprende algo puede decir «Aprendí: …». No se borra con Limpiar actividad.

## Ejecutable de Windows

El archivo `escritorio.spec` empaqueta el programa. El exe abre el navegador en http://127.0.0.1:8765 y no precarga el modelo.

Desde esta carpeta, una vez:

```powershell
$env:PYTHONUTF8 = "1"
.\venv\Scripts\python.exe -m pip install pyinstaller
.\venv\Scripts\python.exe -m PyInstaller --noconfirm escritorio.spec
```

El programa queda en `dist\escritorio\escritorio.exe`. Ábrelo con doble clic. Busca `.env` y `datos` junto al exe o en esta carpeta del proyecto.

Si el puerto 8765 ya está ocupado, cierra el servidor anterior antes de abrir el exe.

## Google Calendar, Tasks y Gmail

1. Entra en https://console.cloud.google.com/ y crea un proyecto, por ejemplo `asistente-personal`.
2. **APIs y servicios → Biblioteca**. Activa Google Calendar API, Google Tasks API y Gmail API.
3. **Pantalla de consentimiento de OAuth**, tipo Externo, en prueba, con tu Gmail como usuario de prueba. Ámbitos:
   - `https://www.googleapis.com/auth/calendar.readonly`
   - `https://www.googleapis.com/auth/tasks.readonly`
   - `https://www.googleapis.com/auth/gmail.compose`
   - `https://www.googleapis.com/auth/gmail.send`
4. **Credenciales → ID de cliente de OAuth → Aplicación de escritorio**. Descarga el JSON.
5. Guárdalo como `credentials.json` en esta carpeta o en `datos\credentials.json`.
6. Pulsa **Conectar Google**. El token queda en `datos\token.json`. No lo compartas.

## Límites

- Solo estas carpetas: Escritorio, Documentos, Descargas y esta carpeta del proyecto.
- Leer y listar no piden aprobación.
- Crear, modificar, borrar, mover y enviar quedan pendientes hasta **Aprobar**.
- No ejecuta comandos ni programas.
- No lee ni modifica `.env`, `credentials.json`, `token.json`, `venv` ni `.git`.
- El servidor solo escucha en `127.0.0.1`.
