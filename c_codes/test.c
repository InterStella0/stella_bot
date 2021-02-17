#include<stdio.h>
#include<stdlib.h>

int main(){
    char** m = calloc(sizeof(char*), 3);
    char* b = "hi";
    m[0] = "test";
    printf("b %d\n", *m[0]);
    free(m[0]);
    m[0] = strdup(b);
    printf("Stuff %d\n", *b);
    printf("a %d\n", *m[0]);

    return 0;
}