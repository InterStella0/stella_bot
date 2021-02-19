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
Result* compile_result(char** array, int size);

Result* find_commands(char** commands, char* string, int n){
    // Returns 2D char array of commands that it found from given string
    size_t found = 1;
    char** found_cmd = calloc(sizeof(char*), found);
    // Remember stella, this iterate each word it founds.
    char* word = strtok(string, " ");
    while(word != NULL) {
        char* target = reverse(word);
        int view = strlen(word) - 1;
        while (view > 0){
            char temporary = target[view];
            target[view] = '\0';
            int index = search(commands, target, n);
            if (index != -1){
                char* command = reverse(commands[index]);
                found_cmd = append(found_cmd, &found, command);
                free(command);
            }
            target[view] = temporary;
            view--;
        }
        free(target);
        word = strtok(NULL, " ");
    }
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
    char** found_prefixes = calloc(sizeof(char*), found);
    while(start > 0){
        int result = search(prefixes, content, n);
        if (result != -1)
            found_prefixes = append(found_prefixes, &found, content);
        content[start-=1] = '\0';
    }
    return compile_result(found_prefixes, found);
}

char** append(char** arr, size_t* size, const char* target){
    // Append new char array into a 2D char array
    arr[*size - 1] = strdup(target);
    return realloc(arr, (*size+=1) * sizeof(char*));
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

void free_result(Result* pointer_result){
    // Free the allocated memory of Result pointer
    char** array = (*pointer_result).found_array;
    int size = (*pointer_result).size;
    // Due to strdup stored in the array, it is required to free from the heap for each element
    for(int i = size - 1; i >= 0; i--)
        free((*pointer_result).found_array[i]);
    // Now we can release the array
    free((*pointer_result).found_array);
    free(pointer_result);
}

char* reverse(char* word){
    // Reverse an array of character.
    int n = strlen(word);
    char* reverse_word = strdup(word);
    for(int i = 0; i < n; i++){
        reverse_word[i] = word[(n - 1) - i];
    }
    return reverse_word;
}