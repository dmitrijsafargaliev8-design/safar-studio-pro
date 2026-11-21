export default async function Listen({params}:{params:Promise<{token:string}>}){
 const {token}=await params;return <iframe className="workspace" allow="autoplay" title="Слепое сравнение мастеров" src={'/listen.html?pair='+encodeURIComponent(token)}/>
}
