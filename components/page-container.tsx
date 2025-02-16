import { cn } from "@/lib/utils"

interface PageContainerProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode
}

export function PageContainer({ children, className, ...props }: PageContainerProps) {
  return (
    <div 
      className={cn(
        "container px-4 py-6 md:py-10 max-w-7xl mx-auto",
        className
      )} 
      {...props}
    >
      {children}
    </div>
  )
} 