package handlers

import (
	"net/http"

	"github.com/a-h/templ"
	"github.com/labstack/echo/v4"

	"coelhonexus/web/components"
)

func render(c echo.Context, component templ.Component) error {
	c.Response().Header().Set(echo.HeaderContentType, "text/html; charset=utf-8")
	return component.Render(c.Request().Context(), c.Response().Writer)
}

func Home(c echo.Context) error {
	return render(c, components.HomePage())
}

func SearchPage(c echo.Context) error {
	return render(c, components.Layout("Search", components.SearchView()))
}

func VideoPage(c echo.Context) error {
	return render(c, components.Layout("Video", components.VideoView()))
}

func ChannelPage(c echo.Context) error {
	return render(c, components.Layout("Channel", components.ChannelView()))
}

func PlaylistPage(c echo.Context) error {
	return render(c, components.Layout("Playlist", components.PlaylistView()))
}

func SearchPartial(c echo.Context) error {
	query := c.FormValue("search_query")
	maxResults := c.FormValue("max_results")
	uploadDate := c.FormValue("upload_date")
	duration := c.FormValue("duration")
	sortBy := c.FormValue("sort_by")
	return render(c, components.ResultsLoading("search", map[string]string{
		"query":       query,
		"max_results": maxResults,
		"upload_date": uploadDate,
		"duration":    duration,
		"sort_by":     sortBy,
	}))
}

func VideoPartial(c echo.Context) error {
	videoURL := c.FormValue("video_url")
	context := c.FormValue("context")
	return render(c, components.ResultsLoading("video", map[string]string{
		"video_url": videoURL,
		"context":   context,
	}))
}

func ChannelPartial(c echo.Context) error {
	channelURL := c.FormValue("channel_url")
	context := c.FormValue("context")
	maxResults := c.FormValue("max_results")
	return render(c, components.ResultsLoading("channel", map[string]string{
		"channel_url": channelURL,
		"context":     context,
		"max_results": maxResults,
	}))
}

func PlaylistPartial(c echo.Context) error {
	playlistURL := c.FormValue("playlist_url")
	context := c.FormValue("context")
	maxResults := c.FormValue("max_results")
	return render(c, components.ResultsLoading("playlist", map[string]string{
		"playlist_url": playlistURL,
		"context":      context,
		"max_results":  maxResults,
	}))
}

func Manifest(c echo.Context) error {
	return c.JSON(http.StatusOK, map[string]interface{}{
		"name":             "COELHO Nexus",
		"short_name":       "COELHONexus",
		"description":      "AI-powered YouTube Content Search & Knowledge Graph",
		"start_url":        "/",
		"display":          "standalone",
		"background_color": "#0a0a0f",
		"theme_color":      "#6c63ff",
		"icons": []map[string]string{
			{"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
			{"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
		},
	})
}

func ServiceWorker(c echo.Context) error {
	c.Response().Header().Set("Content-Type", "application/javascript")
	sw := `
const CACHE_NAME = 'coelhonexus-v1';
const STATIC_ASSETS = ['/static/css/app.css'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS)));
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});
`
	return c.String(http.StatusOK, sw)
}
