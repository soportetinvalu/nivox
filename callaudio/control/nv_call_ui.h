/* SPDX-License-Identifier: MIT */
#ifndef NV_CALL_UI_H
#define NV_CALL_UI_H
/* Shared UI lifecycle policy, executable without Apple frameworks.
 * A visible surface is not the lifetime of an explicitly started call.
 * Pending permission must be cancelled when leaving its initiating surface.
 * Sleep always stops; waking/reopening never starts anything. */
enum { NV_CALL_UI_TAB_CHANGED, NV_CALL_UI_WINDOW_CLOSED, NV_CALL_UI_SLEEP };
static inline int nv_call_ui_should_stop(int event, int permission_pending) {
    switch (event) {
    case NV_CALL_UI_TAB_CHANGED:
    case NV_CALL_UI_WINDOW_CLOSED:
        return permission_pending != 0;
    case NV_CALL_UI_SLEEP:
        return 1;
    default:
        return 0;
    }
}
#endif
