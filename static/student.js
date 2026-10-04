if (window.LOCATION_REQUIRED) {
  const button = document.querySelector('#locate');
  button?.addEventListener('click', () => {
    const message = document.querySelector('#gps-prompt');
    if (!navigator.geolocation) {
      message.textContent = 'Location is not supported by this browser.';
      return;
    }
    button.disabled = true;
    button.textContent = 'Checking location…';
    navigator.geolocation.getCurrentPosition(position => {
      document.querySelector('[name="latitude"]').value = position.coords.latitude;
      document.querySelector('[name="longitude"]').value = position.coords.longitude;
      message.textContent = `Location verified (accuracy about ${Math.round(position.coords.accuracy)} m).`;
      message.className = 'gps-prompt good';
      document.querySelector('#mark-present').disabled = false;
      button.textContent = 'Location verified';
      button.disabled = false;
    }, error => {
      message.textContent = error.code === 1 ? 'Allow location in browser settings, then try again.' : 'Could not get your location. Try again in an open area.';
      message.className = 'gps-prompt bad';
      button.textContent = 'Try location again';
      button.disabled = false;
    }, {enableHighAccuracy: true, timeout: 15000, maximumAge: 0});
  });
}
