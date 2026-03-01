package main

import (
	"log"
	"net/http"
	"os"

	"github.com/labstack/echo/v4"
	"github.com/labstack/echo/v4/middleware"

	"coelhonexus/web/handlers"
)

func main() {
	e := echo.New()

	e.Use(middleware.Logger())
	e.Use(middleware.Recover())
	e.Use(middleware.GzipWithConfig(middleware.GzipConfig{Level: 5}))

	// Static assets
	e.Static("/static", "static")

	// PWA manifest and service worker
	e.GET("/manifest.json", handlers.Manifest)
	e.GET("/sw.js", handlers.ServiceWorker)

	// Pages
	e.GET("/", handlers.Home)
	e.GET("/search", handlers.SearchPage)
	e.GET("/video", handlers.VideoPage)
	e.GET("/channel", handlers.ChannelPage)
	e.GET("/playlist", handlers.PlaylistPage)

	// HTMX partials
	e.POST("/htmx/search", handlers.SearchPartial)
	e.POST("/htmx/video", handlers.VideoPartial)
	e.POST("/htmx/channel", handlers.ChannelPartial)
	e.POST("/htmx/playlist", handlers.PlaylistPartial)

	// Health check
	e.GET("/health", func(c echo.Context) error {
		return c.JSON(http.StatusOK, map[string]string{
			"status":  "healthy",
			"service": "COELHO Nexus Web",
		})
	})

	port := os.Getenv("PORT")
	if port == "" {
		port = "3000"
	}

	log.Printf("COELHO Nexus Web starting on :%s", port)
	if err := e.Start(":" + port); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
