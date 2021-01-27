#include <string.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct ResultStruct{
    char** found_prefixes;
    int size;
}Result;

char** append(char**, size_t*, const char*);
int search(char**, char[], int);
Result* multi_find_prefix(char** prefixes, char content[], int n){
    int start = strlen(content);
    size_t found = 1;
    char** found_prefixes = malloc(sizeof(char*)*found);
    while(start > 0){
        int result = search(prefixes, content, n);
        if (result != -1){
            found_prefixes = append(found_prefixes, &found, content);
        }
        content[start-=1] = '\0';
    }
    Result* pointer_result = malloc(sizeof(Result));
    Result result = {found_prefixes, found};
    *pointer_result = result;
    return pointer_result;
}

char* formatting(char* strvalue);
char* find_prefix(char** prefixes, char content[], int n){
    int start = strlen(content);
    while(start > 0){
        int result = search(prefixes, content, n);
        if (result == -1){
            start--;
            content[start] = '\0';
        }else{
            return formatting(prefixes[result]);
        }
    }
    return formatting("");
}

char** append(char** arr, size_t* size, const char* target){
    arr[*size - 1] = strdup(target);
    return realloc(arr, (*size+=1) * sizeof(char *));
}

int search(char** arr, char target[], int n){
    int low = 0;
    int high = n - 1;
    while (high >= low) {
        int mid = low + (high - low) / 2;
        int result = strcmp(arr[mid], target);
        if(result == 0)
            return mid;
        if(result > 0)
            high = mid - 1;
        else if(result < 0)
            low = mid + 1;
    }
    return -1;
}

char* formatting(char* strvalue){
    char* content = malloc(sizeof(char) * (strlen(strvalue) + 1));
    strcpy(content, strvalue);
    return content;
}

void free_result(Result* pointer_result){
    free(pointer_result);
}
