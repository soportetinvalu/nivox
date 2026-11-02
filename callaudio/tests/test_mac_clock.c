/* SPDX-License-Identifier: MIT
 * Real Darwin timing helper stress: initialize on the control thread before IO.
 */
#include "nv_hal.h"
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
static _Atomic unsigned errors;
static uint64_t expected;
static void *worker(void *arg) {
    (void)arg;
    for (unsigned i = 0; i < 200000; i++)
        if (nv_host_to_ns(1000000000) != expected) atomic_fetch_add(&errors, 1);
    return NULL;
}
int main(void) {
    nv_host_time_init();
    expected = nv_host_to_ns(1000000000);
    if (!expected) return 1;
    pthread_t threads[8];
    for (unsigned i = 0; i < 8; i++) if (pthread_create(&threads[i], NULL, worker, NULL)) return 2;
    for (unsigned i = 0; i < 8; i++) pthread_join(threads[i], NULL);
    unsigned e = atomic_load(&errors);
    printf("{\"ok\":%s,\"threads\":8,\"errors\":%u}\n", e ? "false" : "true", e);
    return e ? 1 : 0;
}
