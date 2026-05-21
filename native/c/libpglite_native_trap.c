#include <setjmp.h>
#include <stdlib.h>

#if defined(LIBPGLITE_NATIVE_BACKEND_TRAMPOLINES)
typedef void (*libpglite_native_void_fn)(void);

extern void PostgresMainLongJmp(void);
extern void PostgresMainLoopOnce(void);
extern void PostgresSendReadyForQueryIfNecessary(void);
extern void PostgresSingleUserMain(int argc, char **argv, const char *username);
extern void pgl_pq_flush(void);
extern void pgl_run_atexit_funcs(void);
extern int pgl_setPGliteActive(int newValue);

static jmp_buf libpglite_native_exit_jmp;
static int libpglite_native_exit_trap_active = 0;
#endif

void libpglite_native_exit(int status) {
#if defined(LIBPGLITE_NATIVE_BACKEND_TRAMPOLINES)
    if (libpglite_native_exit_trap_active) {
        longjmp(libpglite_native_exit_jmp, status == 0 ? 256 : status);
    }
#endif
    exit(status);
}

#if defined(LIBPGLITE_NATIVE_BACKEND_TRAMPOLINES)
static int libpglite_native_trap_void(libpglite_native_void_fn callback) {
    int status = setjmp(libpglite_native_exit_jmp);
    if (status == 0) {
        libpglite_native_exit_trap_active = 1;
        callback();
        libpglite_native_exit_trap_active = 0;
        return 0;
    }
    libpglite_native_exit_trap_active = 0;
    return status == 256 ? 0 : status;
}

int libpglite_native_postgres_single_user_main(
    int argc,
    char **argv,
    const char *username
) {
    int status = setjmp(libpglite_native_exit_jmp);
    if (status == 0) {
        libpglite_native_exit_trap_active = 1;
        PostgresSingleUserMain(argc, argv, username);
        libpglite_native_exit_trap_active = 0;
        return 0;
    }
    libpglite_native_exit_trap_active = 0;
    return status == 256 ? 0 : status;
}

int libpglite_native_postgres_main_loop_once(void) {
    return libpglite_native_trap_void(PostgresMainLoopOnce);
}

int libpglite_native_postgres_main_longjmp(void) {
    return libpglite_native_trap_void(PostgresMainLongJmp);
}

int libpglite_native_postgres_send_ready_for_query_if_necessary(void) {
    return libpglite_native_trap_void(PostgresSendReadyForQueryIfNecessary);
}

int libpglite_native_pgl_pq_flush(void) {
    return libpglite_native_trap_void(pgl_pq_flush);
}

int libpglite_native_pgl_run_atexit_funcs(void) {
    return libpglite_native_trap_void(pgl_run_atexit_funcs);
}

int libpglite_native_pgl_set_active(int newValue) {
    return pgl_setPGliteActive(newValue);
}
#endif
