/* SPDX-License-Identifier: MIT */
#define _POSIX_C_SOURCE 200809L
#include "NoiseProbe.h"
#include <assert.h>
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <sched.h>
#include <string.h>
#ifdef WRAP_ALLOC
static atomic_int tracking, allocations;
void *__real_malloc(size_t); void *__real_calloc(size_t,size_t);
void *__real_realloc(void *,size_t); void __real_free(void *);
void *__wrap_malloc(size_t n) { if(atomic_load(&tracking)) atomic_fetch_add(&allocations,1); return __real_malloc(n); }
void *__wrap_calloc(size_t n,size_t z) { if(atomic_load(&tracking)) atomic_fetch_add(&allocations,1); return __real_calloc(n,z); }
void *__wrap_realloc(void *p,size_t n) { if(atomic_load(&tracking)) atomic_fetch_add(&allocations,1); return __real_realloc(p,n); }
void __wrap_free(void *p) { if(atomic_load(&tracking)) atomic_fetch_add(&allocations,1); __real_free(p); }
#endif
static float signal_at(size_t i) { return ((int)(i%997)-498)/1000.f; }
static np_session *shared;
static atomic_int turn;
static const size_t total=240000;
static void *producer(void *ignored) {
    (void)ignored; float x[127];
    for(size_t pos=0;pos<total;) {
        while(atomic_load(&turn)!=0) sched_yield();
        size_t n=total-pos; if(n>127) n=127;
        for(size_t i=0;i<n;i++) x[i]=signal_at(pos+i);
        int rc=np_push(shared,x,n); assert(rc==NP_OK || rc==NP_LIMIT);
        pos+=n; atomic_store(&turn,1);
    }
    return NULL;
}
static void *consumer(void *ignored) {
    (void)ignored;
    for(size_t pos=0;pos<total;) {
        while(atomic_load(&turn)!=1) sched_yield();
        assert(np_pump(shared)==NP_OK);
        pos=np_received(shared); atomic_store(&turn,0);
    }
    return NULL;
}
int main(void) {
    assert(!np_create(44100,1,100)); assert(!np_create(48000,2,100));
    assert(!np_create(48000,1,0)); assert(!np_create(48000,1,720001));
    np_session *s=np_create(48000,1,1000); assert(s);
    float x[1000]={0}; x[3]=0.5f;
#ifdef WRAP_ALLOC
    atomic_store(&tracking,1);
#endif
    assert(np_push(s,x,1000)==NP_LIMIT); assert(np_pump(s)==NP_OK);
    np_close(s); assert(np_finish(s)==NP_OK);
#ifdef WRAP_ALLOC
    atomic_store(&tracking,0); assert(atomic_load(&allocations)==0);
#endif
    assert(np_count(s)==1000); assert(np_raw(s)[3]==0.5f);
    for(size_t i=0;i<np_count(s);i++) assert(isfinite(np_clean(s)[i]));
    assert(np_push(s,x,1)==NP_CLOSED); assert(np_finish(s)==NP_INVALID);
    np_destroy(s);
    s=np_create(48000,1,720000); assert(s);
    float big[16385]={0}; assert(np_push(s,big,16385)==NP_OVERFLOW);
    assert(np_received(s)==0); np_close(s); assert(np_finish(s)==NP_OVERFLOW); np_destroy(s);
    s=np_create(48000,1,10); assert(s); x[0]=NAN;
    assert(np_push(s,x,1)==NP_INVALID); assert(np_received(s)==0);
    np_close(s); assert(np_finish(s)==NP_INVALID); np_destroy(s);
    shared=np_create(48000,1,total); assert(shared);
    pthread_t p,c; assert(!pthread_create(&p,NULL,producer,NULL)); assert(!pthread_create(&c,NULL,consumer,NULL));
    assert(!pthread_join(p,NULL)); assert(!pthread_join(c,NULL));
    np_close(shared); assert(np_finish(shared)==NP_OK); assert(np_count(shared)==total);
    const float *raw=np_raw(shared), *clean=np_clean(shared);
    for(size_t i=0;i<total;i++) { assert(raw[i]==signal_at(i)); assert(isfinite(clean[i]) && fabsf(clean[i])<=1.f); }
    np_destroy(shared);
    puts("{\"native_assertions\":\"passed\",\"concurrent_frames\":240000,\"allocation_wrapping\":"
#ifdef WRAP_ALLOC
         "true,\"callback_worker_allocations\":0}"
#else
         "false,\"callback_worker_allocations\":null}"
#endif
    );
    return 0;
}
