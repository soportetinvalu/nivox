/* SPDX-License-Identifier: MIT */
#include "nv_call.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct nv_call {
    nv_call_backend b;
    void *context, *routes[2];
    nv_call_device *devices, selected[2];
    size_t count;
    int state, driver;
    char detail[512];
    double last_progress[2];
    uint64_t pushed[2], pulled[2], underruns;
};
static void close_routes(nv_call *c) {
    for (int i = 0; i < 2; i++) {
        if (c->routes[i]) c->b.close(c->context, c->routes[i]);
        c->routes[i] = NULL;
    }
}
static int fail(nv_call *c, const char *message) {
    close_routes(c);
    c->state = NV_CALL_ERROR;
    snprintf(c->detail, sizeof c->detail, "%s", message);
    return -1;
}
nv_call *nv_call_create(const nv_call_backend *b, void *ctx) {
    if (!b || !b->list || !b->driver_ready || !b->open || !b->close || !b->stats || !b->route_valid) return NULL;
    nv_call *c = calloc(1, sizeof *c);
    if (c) { c->b = *b; c->context = ctx; }
    return c;
}
void nv_call_stop(nv_call *c) {
    if (!c) return;
    close_routes(c);
    c->state = NV_CALL_STOPPED;
    c->underruns = 0;
    c->detail[0] = 0;
}
void nv_call_destroy(nv_call *c) {
    if (!c) return;
    nv_call_stop(c);
    free(c->devices);
    free(c);
}
int nv_call_refresh(nv_call *c) {
    nv_call_device *devices = NULL;
    size_t count = 0;
    if (c->b.list(c->context, &devices, &count)) {
        free(devices);
        c->driver = 0;
        return fail(c, "No se pudo consultar CoreAudio. Actualiza los dispositivos y vuelve a intentar.");
    }
    free(c->devices);
    c->devices = devices; c->count = count;
    c->driver = c->b.driver_ready(c->context);
    return 0;
}
static const nv_call_device *find(nv_call *c, const char *uid, int input) {
    if (!uid || !*uid || !strncmp(uid, "nivox.", 6)) return NULL;
    for (size_t i = 0; i < c->count; i++) {
        const nv_call_device *d = &c->devices[i];
        if (!strcmp(uid, d->uid) && d->physical && d->alive &&
            d->rate >= 8000 && (input ? d->inputs : d->outputs)) return d;
    }
    return NULL;
}
int nv_call_start(nv_call *c, const char *mic, const char *out, double now) {
    if (c->state == NV_CALL_STARTING || c->state == NV_CALL_RUNNING) return -1;
    if (nv_call_refresh(c)) return -1;
    if (!c->driver) return fail(c, "Driver Nivox no disponible. Instala NivoxAudio.driver con el instalador y actualiza la lista. La app no instala ni reinicia el audio del sistema.");
    const nv_call_device *m = find(c, mic, 1), *o = find(c, out, 0);
    if (!m || !o) return fail(c, "Selecciona un micrófono físico y una salida física conectados. No se permiten dispositivos virtuales ni se sustituye tu selección por otro dispositivo.");
    c->selected[0] = *m; c->selected[1] = *o;
    c->underruns = 0;
    for (int i = 0; i < 2; i++) {
        char err[256] = {0};
        c->routes[i] = c->b.open(c->context, i, c->selected[i].uid, err, sizeof err);
        if (!c->routes[i]) {
            char message[512];
            snprintf(message, sizeof message, "No se pudo iniciar %s. Revisa permiso de Micrófono, conexión y formato del dispositivo. Detalle: %s",
                     i ? "la salida" : "el micrófono", err);
            return fail(c, message);
        }
        c->pushed[i] = c->pulled[i] = 0;
        c->last_progress[i] = now;
    }
    c->state = NV_CALL_STARTING;
    c->detail[0] = 0;
    return 0;
}
void nv_call_poll(nv_call *c, double now) {
    if (c->state != NV_CALL_STARTING && c->state != NV_CALL_RUNNING) return;
    if (nv_call_refresh(c)) return;
    if (!c->driver) { fail(c, "El driver Nivox dejó de estar disponible. Audio detenido; reinstala o revisa el driver y vuelve a encender."); return; }
    int ready = 1;
    c->underruns = 0;
    for (int i = 0; i < 2; i++) {
        const nv_call_device *d = find(c, c->selected[i].uid, i == 0);
        if (!d || d->rate != c->selected[i].rate || d->inputs != c->selected[i].inputs ||
            d->outputs != c->selected[i].outputs) {
            fail(c, "Dispositivo desconectado o formato cambiado. Ambas rutas se detuvieron; revisa la selección y vuelve a encender. No se cambia a los altavoces automáticamente."); return;
        }
        if (!c->b.route_valid(c->context, c->routes[i])) {
            fail(c, "CoreAudio reiniciado o identidad/formato de ruta cambiado. Ambas rutas se detuvieron; revisa los dispositivos y vuelve a encender."); return;
        }
        nv_call_stats s = {0};
        c->b.stats(c->context, c->routes[i], &s);
        c->underruns += s.underruns + s.overflows;
        if (s.rejected) { fail(c, "El motor rechazó muestras de audio. Ambas rutas se detuvieron; revisa el formato y vuelve a encender."); return; }
        if (s.pushed > c->pushed[i] && s.pulled > c->pulled[i]) {
            c->last_progress[i] = now;
            c->pushed[i] = s.pushed; c->pulled[i] = s.pulled;
        }
        if (now - c->last_progress[i] >= 5) {
            fail(c, "No hay actividad de captura/reproducción durante 5 s. Audio detenido. Revisa permiso de Micrófono en Privacidad y seguridad, driver y conexión; luego vuelve a encender."); return;
        }
        if (!s.primed || !s.pushed || !s.pulled) ready = 0;
    }
    c->state = ready ? NV_CALL_RUNNING : NV_CALL_STARTING;
}
int nv_call_state(const nv_call *c) { return c->state; }
int nv_call_driver_ready(const nv_call *c) { return c->driver; }
const char *nv_call_detail(const nv_call *c) { return c->detail; }
uint64_t nv_call_underruns(const nv_call *c) { return c->underruns; }
size_t nv_call_device_count(const nv_call *c) { return c->count; }
const nv_call_device *nv_call_device_at(const nv_call *c, size_t i) { return i < c->count ? &c->devices[i] : NULL; }
const char *nv_call_device_uid(const nv_call_device *d) { return d->uid; }
const char *nv_call_device_name(const nv_call_device *d) { return d->name; }
