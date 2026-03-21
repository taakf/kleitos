/*
 * Axion — Native macOS Application Binary
 * Copyright (c) 2026 4Labs. All rights reserved.
 *
 * This is the real CFBundleExecutable for Axion.app. Because the BINARY
 * is named "Axion" (and embeds the Python interpreter via the C API),
 * macOS shows "Axion" in Force Quit, Activity Monitor, Login Items,
 * and all system identity surfaces.
 *
 * Architecture:
 *   1. On first launch, if runtime is not set up, call axion-bootstrap
 *      (bash script) to install Python, venv, and dependencies.
 *   2. Start the uvicorn backend server as a subprocess.
 *   3. Initialize embedded Python and run axion-app.pyw in-process.
 *   4. axion-app.pyw creates the pywebview native window.
 *
 * The result is a SINGLE process named "Axion" that owns the window,
 * the Dock icon, and the menu bar.
 *
 * Build:
 *   PYTHON_INC=$(python3.12-config --includes | sed 's/-I//g' | awk '{print $1}')
 *   PYTHON_LIB=$(python3.12-config --ldflags --embed | sed 's/-L//' | awk '{print $1}')
 *   cc -o Axion scripts/axion-native.c \
 *       -I"$PYTHON_INC" -L"$PYTHON_LIB" -lpython3.12 \
 *       -ldl -framework CoreFoundation -framework Foundation \
 *       -Wno-deprecated-declarations
 *
 *   # CRITICAL: Copy binary into bundle THEN ad-hoc sign the full bundle.
 *   # Without this, Finder launch will SIGKILL with "Code Signature Invalid".
 *   cp Axion Axion.app/Contents/MacOS/Axion
 *   codesign --force --deep --sign - Axion.app
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <spawn.h>
#include <sys/wait.h>
#include <mach-o/dyld.h>
#include <libgen.h>

/* ---------------------------------------------------------------------------
 * Globals
 * --------------------------------------------------------------------------- */
static pid_t g_server_pid = 0;
static char g_data_dir[4096];
static char g_runtime_dir[4096];
static char g_venv_dir[4096];
static char g_venv_site[4096];
static char g_bundle_dir[4096];
static char g_script_path[4096];

extern char **environ;

/* ---------------------------------------------------------------------------
 * Logging
 * --------------------------------------------------------------------------- */
static FILE *g_log_fp = NULL;

static void ax_log(const char *fmt, ...) {
    va_list args;
    if (!g_log_fp) {
        char log_path[4096];
        snprintf(log_path, sizeof(log_path), "%s/logs/native.log", g_data_dir);
        g_log_fp = fopen(log_path, "a");
    }
    if (g_log_fp) {
        time_t now = time(NULL);
        struct tm *tm = localtime(&now);
        fprintf(g_log_fp, "[%04d-%02d-%02d %02d:%02d:%02d] ",
                tm->tm_year+1900, tm->tm_mon+1, tm->tm_mday,
                tm->tm_hour, tm->tm_min, tm->tm_sec);
        va_start(args, fmt);
        vfprintf(g_log_fp, fmt, args);
        va_end(args);
        fprintf(g_log_fp, "\n");
        fflush(g_log_fp);
    }
    /* Also print to stderr for debugging */
    va_start(args, fmt);
    vfprintf(stderr, fmt, args);
    va_end(args);
    fprintf(stderr, "\n");
}

/* ---------------------------------------------------------------------------
 * Signal handling — clean up server on exit
 * --------------------------------------------------------------------------- */
static void cleanup_server(void) {
    if (g_server_pid > 0) {
        ax_log("Stopping server (PID %d)", g_server_pid);
        kill(g_server_pid, SIGTERM);
        int status;
        int waited = 0;
        while (waitpid(g_server_pid, &status, WNOHANG) == 0 && waited < 5) {
            usleep(500000);
            waited++;
        }
        if (waitpid(g_server_pid, &status, WNOHANG) == 0) {
            kill(g_server_pid, SIGKILL);
            waitpid(g_server_pid, &status, 0);
        }
        g_server_pid = 0;
    }
    if (g_log_fp) {
        ax_log("Axion exiting");
        fclose(g_log_fp);
        g_log_fp = NULL;
    }
}

static void signal_handler(int sig) {
    cleanup_server();
    _exit(sig == SIGTERM ? 0 : 1);
}

/* ---------------------------------------------------------------------------
 * Path resolution
 * --------------------------------------------------------------------------- */
static int resolve_paths(void) {
    /* Find our own bundle location */
    char exe_path[4096];
    uint32_t size = sizeof(exe_path);
    if (_NSGetExecutablePath(exe_path, &size) != 0) return -1;

    char *real_exe = realpath(exe_path, NULL);
    if (!real_exe) return -1;

    /* Contents/MacOS/Axion -> Contents */
    char *macos_dir = dirname(strdup(real_exe));
    char contents_path[4096];
    snprintf(contents_path, sizeof(contents_path), "%s/..", macos_dir);
    char *resolved = realpath(contents_path, NULL);
    if (!resolved) { free(real_exe); return -1; }
    strncpy(g_bundle_dir, resolved, sizeof(g_bundle_dir) - 1);
    free(resolved);
    free(real_exe);

    /* Build stable runtime paths under ~/kleitos-data/ */
    const char *home = getenv("HOME");
    if (!home) return -1;

    snprintf(g_data_dir, sizeof(g_data_dir), "%s/kleitos-data", home);
    snprintf(g_runtime_dir, sizeof(g_runtime_dir), "%s/app", g_data_dir);
    snprintf(g_venv_dir, sizeof(g_venv_dir), "%s/.venv", g_data_dir);
    snprintf(g_venv_site, sizeof(g_venv_site),
             "%s/lib/python3.12/site-packages", g_venv_dir);
    snprintf(g_script_path, sizeof(g_script_path),
             "%s/scripts/axion-app.pyw", g_runtime_dir);

    return 0;
}

/* ---------------------------------------------------------------------------
 * Bootstrap check — run axion-bootstrap if not set up
 * --------------------------------------------------------------------------- */
static int ensure_bootstrapped(void) {
    char python_path[4096];
    snprintf(python_path, sizeof(python_path), "%s/bin/python", g_venv_dir);

    /* Check if venv python exists AND script exists */
    if (access(python_path, X_OK) == 0 && access(g_script_path, R_OK) == 0) {
        /* Quick import check */
        char check_cmd[8192];
        snprintf(check_cmd, sizeof(check_cmd),
                 "%s -c 'import fastapi, uvicorn, sqlalchemy' 2>/dev/null",
                 python_path);
        if (system(check_cmd) == 0) {
            ax_log("Runtime is healthy — skipping bootstrap");
            return 0;
        }
    }

    ax_log("Runtime needs bootstrap");

    /* Run the bootstrap script */
    char bootstrap_path[4096];
    snprintf(bootstrap_path, sizeof(bootstrap_path),
             "%s/MacOS/axion-bootstrap", g_bundle_dir);

    if (access(bootstrap_path, X_OK) != 0) {
        ax_log("ERROR: Bootstrap script not found at %s", bootstrap_path);
        return -1;
    }

    ax_log("Running bootstrap: %s", bootstrap_path);
    int ret = system(bootstrap_path);
    if (ret != 0) {
        ax_log("ERROR: Bootstrap failed (exit %d)", ret);
        return -1;
    }

    ax_log("Bootstrap complete");
    return 0;
}

/* ---------------------------------------------------------------------------
 * Start uvicorn server
 * --------------------------------------------------------------------------- */
static int start_server(void) {
    char python_path[4096];
    snprintf(python_path, sizeof(python_path), "%s/bin/python", g_venv_dir);

    char stdout_log[4096], stderr_log[4096];
    snprintf(stdout_log, sizeof(stdout_log), "%s/logs/kleitos-stdout.log", g_data_dir);
    snprintf(stderr_log, sizeof(stderr_log), "%s/logs/kleitos-stderr.log", g_data_dir);

    /* Check if server is already running */
    int health_check = system(
        "curl -s -o /dev/null -w '%{http_code}' "
        "http://127.0.0.1:7777/api/v1/health 2>/dev/null | grep -q 200"
    );
    if (health_check == 0) {
        ax_log("Server already running");
        return 0;
    }

    ax_log("Starting uvicorn server...");

    pid_t pid = fork();
    if (pid < 0) {
        ax_log("ERROR: fork() failed");
        return -1;
    }

    if (pid == 0) {
        /* Child: become the uvicorn server */
        /* Redirect stdout/stderr to log files */
        freopen(stdout_log, "a", stdout);
        freopen(stderr_log, "a", stderr);

        /* Set environment */
        setenv("KLEITOS_DATA_DIR", g_data_dir, 1);
        setenv("KLEITOS_DB_PATH", g_data_dir, 1);
        char db_path[4096];
        snprintf(db_path, sizeof(db_path), "%s/db/kleitos.db", g_data_dir);
        setenv("KLEITOS_DB_PATH", db_path, 1);

        /* cd to runtime dir */
        chdir(g_runtime_dir);

        /* exec uvicorn */
        execl(python_path, "python", "-m", "uvicorn", "src.main:app",
              "--host", "127.0.0.1", "--port", "7777", NULL);
        _exit(1);
    }

    /* Parent: wait for health */
    g_server_pid = pid;
    ax_log("Server started (PID %d)", pid);

    char pid_file[4096];
    snprintf(pid_file, sizeof(pid_file), "%s/kleitos.pid", g_data_dir);
    FILE *pf = fopen(pid_file, "w");
    if (pf) { fprintf(pf, "%d", pid); fclose(pf); }

    for (int i = 0; i < 45; i++) {
        /* Check if child died */
        int status;
        if (waitpid(pid, &status, WNOHANG) != 0) {
            ax_log("ERROR: Server died during startup");
            g_server_pid = 0;
            return -1;
        }
        /* Check health */
        int hc = system(
            "curl -s -o /dev/null -w '%{http_code}' "
            "http://127.0.0.1:7777/api/v1/health 2>/dev/null | grep -q 200"
        );
        if (hc == 0) {
            ax_log("Server healthy after %ds", i + 1);
            return 0;
        }
        usleep(1000000); /* 1 second */
    }

    ax_log("ERROR: Server startup timed out");
    return -1;
}

/* ---------------------------------------------------------------------------
 * Run the Python GUI app (in-process via Python C API)
 * --------------------------------------------------------------------------- */
static int run_python_app(void) {
    ax_log("Initializing embedded Python...");

    /* Set program name to "Axion" */
    wchar_t *program = Py_DecodeLocale("Axion", NULL);
    if (!program) return -1;
    Py_SetProgramName(program);

    /* Set PYTHONPATH to include venv site-packages */
    setenv("PYTHONPATH", g_venv_site, 1);
    setenv("VIRTUAL_ENV", g_venv_dir, 1);
    setenv("AXION_BUNDLE_DIR", g_bundle_dir, 1);
    setenv("AXION_RUNTIME_DIR", g_runtime_dir, 1);
    setenv("AXION_DATA_DIR", g_data_dir, 1);

    Py_Initialize();

    /* Set sys.argv = ["Axion", "script_path", "--dev"] */
    wchar_t *py_argv[3];
    py_argv[0] = Py_DecodeLocale("Axion", NULL);
    py_argv[1] = Py_DecodeLocale(g_script_path, NULL);
    py_argv[2] = Py_DecodeLocale("--dev", NULL);
    PySys_SetArgvEx(3, py_argv, 0);

    /* Ensure venv site-packages is in sys.path */
    char setup_code[4096];
    snprintf(setup_code, sizeof(setup_code),
        "import sys, os, site\n"
        "venv_site = '%s'\n"
        "if venv_site not in sys.path:\n"
        "    sys.path.insert(0, venv_site)\n"
        "    site.addsitedir(venv_site)\n"
        "os.chdir('%s')\n",
        g_venv_site, g_runtime_dir);

    if (PyRun_SimpleString(setup_code) != 0) {
        ax_log("ERROR: Python setup failed");
        Py_Finalize();
        return -1;
    }

    ax_log("Running axion-app.pyw...");

    /* Run the script */
    FILE *fp = fopen(g_script_path, "r");
    if (!fp) {
        ax_log("ERROR: Cannot open %s", g_script_path);
        Py_Finalize();
        return -1;
    }

    int result = PyRun_SimpleFile(fp, g_script_path);
    fclose(fp);

    if (result != 0) {
        ax_log("Python app exited with error");
    }

    Py_Finalize();
    PyMem_RawFree(program);
    return result;
}

/* ---------------------------------------------------------------------------
 * macOS notification helper — uses NSUserNotificationCenter via Python
 *
 * We avoid osascript because macOS attributes osascript notifications
 * to "Script Editor", not to the calling app. Instead, we use PyObjC
 * through the embedded Python interpreter (after Py_Initialize).
 * For pre-Python notifications (errors before Python starts), we log only.
 * --------------------------------------------------------------------------- */
static void notify(const char *message) {
    /* Only works after Python is initialized */
    if (!Py_IsInitialized()) {
        ax_log("Notification (pre-Python): %s", message);
        return;
    }
    char py_cmd[4096];
    snprintf(py_cmd, sizeof(py_cmd),
        "try:\n"
        "    from Foundation import NSUserNotification, NSUserNotificationCenter\n"
        "    _n = NSUserNotification.alloc().init()\n"
        "    _n.setTitle_('Axion')\n"
        "    _n.setInformativeText_('%s')\n"
        "    NSUserNotificationCenter.defaultUserNotificationCenter()"
        ".deliverNotification_(_n)\n"
        "except Exception:\n"
        "    pass\n",
        message);
    PyRun_SimpleString(py_cmd);
}

/* ---------------------------------------------------------------------------
 * Main
 * --------------------------------------------------------------------------- */
int main(int argc, char *argv[]) {
    /* Set up signal handling */
    signal(SIGTERM, signal_handler);
    signal(SIGINT, signal_handler);
    atexit(cleanup_server);

    /* Resolve paths */
    if (resolve_paths() != 0) {
        fprintf(stderr, "Axion: Cannot resolve paths\n");
        return 1;
    }

    /* Create data directories */
    char cmd[4096];
    snprintf(cmd, sizeof(cmd),
             "mkdir -p '%s/db' '%s/logs' '%s/backups' '%s'",
             g_data_dir, g_data_dir, g_data_dir, g_runtime_dir);
    system(cmd);

    ax_log("Axion starting");
    ax_log("  Bundle: %s", g_bundle_dir);
    ax_log("  Data: %s", g_data_dir);
    ax_log("  Runtime: %s", g_runtime_dir);
    ax_log("  Venv: %s", g_venv_dir);

    /* Bootstrap if needed */
    if (ensure_bootstrapped() != 0) {
        ax_log("FATAL: Setup failed");
        /* Can't use notify() here — Python not initialized yet */
        return 1;
    }

    /* Start server */
    if (start_server() != 0) {
        ax_log("FATAL: Server failed to start");
        return 1;
    }

    /* Run the Python GUI (blocks until window closed).
     * The "Axion is ready!" notification is sent from axion-app.pyw
     * after Python + PyObjC are initialized, so it comes from the
     * Axion app identity, not from Script Editor. */
    int result = run_python_app();

    /* Cleanup happens via atexit */
    return result;
}
