package templates

import "strconv"

// formatIdx — citation index label ("1", "2", …). Keeping a Go helper so the
// .templ file doesn't have to call the stdlib formatter inline.
func formatIdx(n int) string {
	return strconv.Itoa(n)
}
