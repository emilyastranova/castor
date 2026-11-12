package main

import (
	"fmt"
	"net/http"

	"example.com/shop/internal/store"
	"github.com/go-chi/chi/v5"
)

func main() {
	r := chi.NewRouter()
	http.HandleFunc("/health", func(w http.ResponseWriter, _ *http.Request) { fmt.Fprint(w, store.Ping()) })
	http.ListenAndServe(":8080", r)
}
