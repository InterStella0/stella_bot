#include <string.h>
#include <stdio.h>
#include <stdlib.h>
int search(char** x, char[], int);
char* find_prefix(char** prefixes, char content[], int n);
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