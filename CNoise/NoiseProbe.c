/* SPDX-License-Identifier: MIT */
#include "NoiseProbe.h"
#include "christian_noise.h"
#include <stdatomic.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <sched.h>
#define QUEUE 16384u
#define SCRATCH 4096u
_Static_assert((int)NP_DELAY == (int)CN_LATENCY, "SpeexDSP delay mismatch");
struct np_session {
    cn_engine *engine;
    atomic_size_t read, written;
    atomic_int accepting, active, error;
    size_t max, processed;
    int finished;
    int timed;
    int64_t next_sample_time;
    atomic_size_t blocks;
    float scratch[SCRATCH];
    float queue[QUEUE];
    float *raw, *wet;
};
np_session *np_create(unsigned rate, unsigned channels, size_t max_frames) {
    if (rate != NP_RATE || channels != 1 || !max_frames || max_frames > NP_MAX_FRAMES) return NULL;
    np_session *s=calloc(1,sizeof(*s));
    if (!s) return NULL;
    atomic_init(&s->read,0); atomic_init(&s->written,0);
    atomic_init(&s->active,0); atomic_init(&s->accepting,1); atomic_init(&s->error,0);
    atomic_init(&s->blocks,0);
    if (!atomic_is_lock_free(&s->read) || !atomic_is_lock_free(&s->written) ||
        !atomic_is_lock_free(&s->active) || !atomic_is_lock_free(&s->blocks) || !atomic_is_lock_free(&s->accepting) ||
        !atomic_is_lock_free(&s->error)) { free(s); return NULL; }
    s->max=max_frames;
    s->raw=calloc(max_frames,sizeof(float));
    s->wet=calloc(max_frames+NP_DELAY,sizeof(float));
    s->engine=cn_create(NP_RATE,1,1.0f);
    if (!s->raw || !s->wet || !s->engine) { np_destroy(s); return NULL; }
    return s;
}
void np_close(np_session *s) {
    if (!s) return;
    atomic_store(&s->accepting,0);
    while (atomic_load(&s->active)) sched_yield();
}
void np_destroy(np_session *s) {
    if (!s) return;
    np_close(s); cn_destroy(s->engine); free(s->raw); free(s->wet); free(s);
}
static int push(np_session *s,const float *input,size_t count,int timed,int64_t sample_time) {
    if (!s || (count && !input)) return NP_INVALID;
    atomic_store(&s->active,1); /* exactly one producer, never two taps */
    int rc=NP_OK;
    if (!atomic_load(&s->accepting)) { rc=NP_CLOSED; goto done; }
    rc=atomic_load(&s->error); if (rc) goto done;
    if (timed && (count > INT64_MAX || sample_time > INT64_MAX-(int64_t)count ||
                  (s->timed && sample_time != s->next_sample_time))) {
        rc=NP_INVALID; atomic_store(&s->error,rc); goto done;
    }
    if (timed) { s->timed=1; s->next_sample_time=sample_time+(int64_t)count; }
    size_t w=atomic_load(&s->written), r=atomic_load(&s->read);
    size_t n=count < s->max-w ? count : s->max-w;
    if (n > QUEUE-(w-r)) { rc=NP_OVERFLOW; atomic_store(&s->error,rc); goto done; }
    for (size_t i=0;i<n;i++) if (!isfinite(input[i]) || fabsf(input[i])>1.f) {
        rc=NP_INVALID; atomic_store(&s->error,rc); goto done;
    }
    for (size_t i=0;i<n;i++) s->queue[(w+i)%QUEUE]=input[i];
    atomic_store(&s->written,w+n);
    rc=w+n==s->max ? NP_LIMIT : NP_OK;
done:
    atomic_store(&s->active,0); return rc;
}
int np_push(np_session *s,const float *input,size_t count) { return push(s,input,count,0,0); }
int np_push_timed(np_session *s,const float *input,size_t count,int64_t sample_time) {
    return push(s,input,count,1,sample_time);
}
/* HAL/IOProc producer: copy one channel out of a strided Float32 block into
 * session-owned scratch (no allocation) and push it with the device sample time.
 * Same single-producer contract as np_push_timed. Malformed layout is sticky. */
int np_push_strided_timed(np_session *s,const float *base,size_t frames,size_t stride,size_t offset,int64_t sample_time) {
    if (!s) return NP_INVALID;
    if (!base || !stride || stride>1024 || offset>=stride || frames>(size_t)INT32_MAX) {
        atomic_store(&s->error,NP_INVALID); return NP_INVALID;
    }
    atomic_fetch_add(&s->blocks,1);
    size_t done=0; int rc=NP_OK;
    while (done<frames) {
        if (sample_time>0 && done>(size_t)(INT64_MAX-sample_time)) {
            atomic_store(&s->error,NP_INVALID); return NP_INVALID;
        }
        size_t n=frames-done<SCRATCH ? frames-done : SCRATCH;
        const float *p=base+done*stride+offset;
        for (size_t i=0;i<n;i++) s->scratch[i]=p[i*stride];
        rc=push(s,s->scratch,n,1,sample_time+(int64_t)done);
        if (rc) break;
        done+=n;
    }
    return rc;
}
size_t np_blocks(const np_session *s) { return s ? atomic_load(&s->blocks) : 0; }
int np_pump(np_session *s) {
    if (!s || s->finished) return NP_INVALID;
    int error=atomic_load(&s->error); if (error) return error;
    size_t r=atomic_load(&s->read), w=atomic_load(&s->written);
    while (r<w) {
        size_t n=w-r; if (n>QUEUE-r%QUEUE) n=QUEUE-r%QUEUE;
        memcpy(s->raw+r,s->queue+r%QUEUE,n*sizeof(float));
        if (cn_process(s->engine,s->raw+r,s->wet+r,n)) {
            atomic_store(&s->error,NP_DSP); return NP_DSP;
        }
        r+=n; s->processed=r; atomic_store(&s->read,r);
    }
    return NP_OK;
}
int np_finish(np_session *s) {
    if (!s || atomic_load(&s->accepting) || s->finished) return NP_INVALID;
    int rc=np_pump(s); if (rc) return rc;
    if (!s->processed) return NP_INVALID;
    float zeros[NP_DELAY]={0};
    if (cn_process(s->engine,zeros,s->wet+s->processed,NP_DELAY)) return NP_DSP;
    s->finished=1; return NP_OK;
}
void np_abort(np_session *s) { if (s) atomic_store(&s->error,NP_INVALID); }
int np_error(const np_session *s) { return s ? atomic_load(&s->error) : NP_INVALID; }
size_t np_received(const np_session *s) { return s ? atomic_load(&s->written) : 0; }
size_t np_count(const np_session *s) { return s && s->finished ? s->processed : 0; }
const float *np_raw(const np_session *s) { return s && s->finished ? s->raw : NULL; }
const float *np_clean(const np_session *s) { return s && s->finished ? s->wet+NP_DELAY : NULL; }
