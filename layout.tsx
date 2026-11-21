import type {Metadata} from 'next';
import './global.css';
export const metadata:Metadata={title:'SAFAR STUDIO',description:'Мультитрековая студия SAFAR. Микшер, мастеринг и Blind A/B.'};
export default function Layout({children}:{children:React.ReactNode}){return <html lang="ru"><body>{children}</body></html>}
