#include <string.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct ResultStruct{
    char** found_array;
    int size;
}Result;

char** append(char**, size_t*, const char*);
int search(char**, char[], int);
char* reverse(char*);
void sorting(char**, int*, size_t);
char* formatting(char* strvalue);
Result* compile_result(char** array, int size);

Result* find_commands(char** commands, char* string, int n){
    // Returns 2D char array of commands that it found from given string
    size_t found = 1;
    char** found_cmd = calloc(sizeof(char*), found);
    char** reverse_cmd =  calloc(sizeof(char*), n);
    int* pos =  calloc(sizeof(int), n);
    for(int i = 0; i < n; i++){
        pos[i] = i;
        reverse_cmd[i] = reverse(commands[i]);
    }
    sorting(reverse_cmd, pos, n);
    // Remember stella, this iterate each word it founds.
    char* word = strtok(string, " ");
    while(word != NULL) {
        char* target = reverse(word);
        int view = strlen(word);
        while (view > 0){
            target[view--] = '\0';
            int index = search(reverse_cmd, target, n);
            if (index != -1)
                found_cmd = append(found_cmd, &found, commands[pos[index]]);
        }
        free(target);
        word = strtok(NULL, " ");
    }
    free(reverse_cmd);
    free(pos);
    return compile_result(found_cmd, found);
}

Result* compile_result(char** array, int size){
    // Creates a struct pointer to return to Python
    Result* pointer_result = malloc(sizeof(Result));
    Result result = {array, size - 1};
    *pointer_result = result;
    return pointer_result;
}

Result* multi_find_prefix(char** prefixes, char content[], int n){
    // Creates a 2D char array of prefixes it found from content
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
    return compile_result(found_prefixes, found);
}

char* find_prefix(char** prefixes, char content[], int n){
    // Finds a single prefix from 2D char array of prefixes
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
    // Append new char array into a 2D char array
    arr[*size - 1] = strdup(target);
    return realloc(arr, (*size+=1) * sizeof(char *));
}

int search(char** arr, char target[], int n){
    // Binary search for 2D array and return the index of target, -1 if it can't find it
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
    // Allocate memory for strvalue that was being passed to be return to Python
    char* content = malloc(sizeof(char) * (strlen(strvalue) + 1));
    strcpy(content, strvalue);
    return content;
}

void free_result(Result* pointer_result){
    // Free the allocated memory of Result pointer
    free((*pointer_result).found_array);
    free(pointer_result);
}

char* reverse(char* word){
    // Reverse an array of character, make sure to free the memory allocated.
    int n = strlen(word);
    char* reverse_word = calloc(sizeof(char), n);
    for(int i = 0; i < n; i++){
        reverse_word[i] = word[(n - 1) - i];
    }
    return reverse_word;
}

void sorting(char** current, int* pos, size_t n){
    // Uses Insertion sort for 2D char array
    for (size_t i = 1; i < n; i++){
        char* key = strdup(current[i]);
        int new_post = pos[i];
        int j = i - 1;
        while (j >= 0 && (strcmp(current[j], key) > 0)){
            current[j + 1] = strdup(current[j]);  
            pos[j + 1] = pos[j];
            j--;
        }
        current[j + 1] = strdup(key);
        pos[j + 1] = new_post;
    }  
    return;
}