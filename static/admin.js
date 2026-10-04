document.querySelectorAll('.start-form').forEach(form => {
  form.addEventListener('submit', event => {
    if (!window.LOCATION_REQUIRED) return;
    event.preventDefault();
    const error = document.querySelector('#location-error');
    const button = form.querySelector('button');
    if (!navigator.geolocation) {
      error.textContent = 'This browser cannot provide location. Turn off location verification in .env or use a supported phone browser.';
      error.classList.remove('hidden');
      return;
    }
    button.disabled = true;
    button.textContent = 'Getting location…';
    navigator.geolocation.getCurrentPosition(position => {
      form.querySelector('[name="latitude"]').value = position.coords.latitude;
      form.querySelector('[name="longitude"]').value = position.coords.longitude;
      form.submit();
    }, () => {
      button.disabled = false;
      button.textContent = 'Create QR →';
      error.textContent = 'Location was blocked. Allow location in browser settings and try again.';
      error.classList.remove('hidden');
    }, {enableHighAccuracy: true, timeout: 15000, maximumAge: 0});
  });
});
