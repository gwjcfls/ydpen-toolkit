/* dltest.c ?? ?????????????????? dlopen ?? */
#include <dlfcn.h>
#include <stdio.h>
int main(int argc, char **argv) {
    void *h;
    h = dlopen("/usr/lib/libquickjs.so", RTLD_NOW | RTLD_GLOBAL);
    printf("libquickjs.so   : %s\n", h ? "ok" : dlerror());
    h = dlopen("/usr/lib/libfalcon.so", RTLD_NOW | RTLD_GLOBAL);
    printf("libfalcon.so    : %s\n", h ? "ok" : dlerror());
    h = dlopen("/usr/lib/libjsapi_proxy.so", RTLD_NOW | RTLD_GLOBAL);
    printf("libjsapi_proxy  : %s\n", h ? "ok" : dlerror());
    if (argc > 1) {
        h = dlopen(argv[1], RTLD_NOW);
        printf("plugin %s\n  -> %s\n", argv[1], h ? "LOADED ok" : dlerror());
        if (h) {
            void *sym = dlsym(h, "custom_init_jsapis");
            printf("  custom_init_jsapis: %s\n", sym ? "found" : dlerror());
        }
    }
    return 0;
}
