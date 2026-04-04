import './App.css'

function App() {

  function handleClick() {
    fetch('/print', {
      method: 'POST'
    }).then(response => response.json())
    .then(data => {
      console.log(data)
    })
    .catch(error => {
      console.error('Error:', error)
    });
  }

  return (
    <>
      <section id="center">
        <button
          className="counter"
          onClick={handleClick}
        >
          Print From Server
        </button>
      </section>


    </>
  )
}

export default App
