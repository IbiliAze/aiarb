package queue

import "testing"

func TestPosition(t *testing.T) {
	if Position("a") != 0 {
		t.Fail()
	}
}
